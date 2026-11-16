"""Exercise the real bundled study engine without contacting the live website.

Run with the website's .venv Python. All writes stay in .test-data; public
library snapshots are read-only except for one isolated reader database copy.
"""
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'Python/app'))
sys.path.insert(0, str(PROJECT / 'server_integration'))
from offline_app import create_app, TERMS_VERSION, WebsiteBridge, file_hash
from mobile_bible_api import register_mobile_bible_routes
from flask import Flask

API = '/pastor-resources/bible-study/api/'
PROFILE = dict(user_key='pastor:offline-test', username='offline-test', role='pastor',
               name='Offline Test', token='test-device-token', can_convert=False)
DOCUMENT = dict(schema_version=1, theme='Offline faithfulness', text='John 3:16', content=dict(blocks=[
    dict(type='paragraph', align='center', runs=[dict(text='Faith, pagtuo, pananampalataya — ἀγάπη',
         bold=True, italic=True, underline=True, font_size=16, color='#123456', highlight='#fffeb3')])]))


class FakeBridge:
    calls = []
    connected = True
    records = {}
    revision = 1

    def __init__(self, profile): self.profile = profile

    def call(self, path, payload=None, authenticated=True):
        self.calls.append((path, payload))
        if not self.connected: raise ValueError('Offline test: no network.')
        if path == 'login': return dict(ok=True, profile=dict(PROFILE))
        if path.startswith('website-sync/'): return dict(ok=True, job=dict(running=False))
        if path == 'drive':
            if payload['action'] == 'list': return dict(ok=True, notes=[self.summary(record) for record in self.records.values()], index_version=self.revision, next_after='')
            if payload['action'] == 'get': return dict(ok=True, record=self.records[payload['note_id']])
            if payload['action'] in ('put', 'delete'):
                import bible_study
                record = dict(note_id=payload['note_id'], version=payload['base_version']+1, deleted=payload['action']=='delete',
                    user_key=bible_study.sermon_notes_drive_key(self.profile['user_key']), request_id=payload['request_id'])
                if 'note' in payload: record['note'] = payload['note']
                self.records[record['note_id']] = record
                return dict(ok=True, item=self.summary(record))
        raise ValueError('Unexpected test bridge request: ' + path)

    @staticmethod
    def summary(record):
        return dict(note_id=record['note_id'], version=record['version'], deleted=record['deleted'],
                    sha256=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest())


def prepared_app(data_dir=None):
    root = Path(data_dir) if data_dir else PROJECT / '.test-data/reader'
    root.mkdir(parents=True, exist_ok=True)
    core = root / 'study/bible_study.db'
    if not core.exists():
        core.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / 'Resources/study/bible_study.db', core)
    app = create_app(root, PROJECT / 'Resources/runtime',
                     PROJECT / 'Resources/resource-manifest.json', gate='test-gate', bridge_factory=FakeBridge)
    app.config.update(TESTING=True, SERMON_NOTES_JOBS_INLINE=True)
    app.config['AI_DATABASE'] = str(PROJECT / 'Resources/library/study_library.db')
    app.config['DATABASE'] = str(PROJECT / 'Resources/library/catalogue.db')
    app.config['BIBLE_STUDY_LIBRARY_DATABASE'] = app.config['DATABASE']
    app.config['BIBLE_ILLUSTRATIONS_DATABASE'] = str(PROJECT / 'Resources/study/sermon_illustrations.db')
    app._offline_owner.DATABASE = app.config['DATABASE']
    app._offline_owner.AI_DATABASE = app.config['AI_DATABASE']
    app.device_state.save_profile(PROFILE)
    app.device_state.save_setting(agreement=TERMS_VERSION, resources_ready=True)
    return app


class OfflineEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = prepared_app()
        cls.client = cls.app.test_client()
        cls.client.set_cookie('iowo_device_gate', 'test-gate')
        cls.client.get('/')
        with cls.client.session_transaction() as identity: cls.csrf = identity['bible_study_csrf']

    def setUp(self):
        FakeBridge.connected = True
        FakeBridge.calls.clear()
        self.app.device_state.save_profile(PROFILE)

    def get(self, path):
        response = self.client.get(API + path)
        self.assertEqual(response.status_code, 200, (path, response.get_data(as_text=True)[:300]))
        result = response.get_json()
        self.assertTrue(result.get('ok', True), result)
        return result

    def write(self, path, payload, method='POST'):
        return self.client.open(API + path, method=method, json=payload, headers={'X-CSRF-Token': self.csrf})

    def test_01_gate_and_csrf(self):
        self.assertEqual(self.app.test_client().get('/').status_code, 403)
        self.assertEqual(self.client.post('/mobile/agree', json={'accepted': True}).status_code, 400)
        self.assertEqual(self.client.get('/', headers={'Origin': 'https://other.invalid'}).status_code, 403)
        self.assertEqual(self.client.post(API+'bookmarks', json={'verse_id': 1}).status_code, 400)

    def test_02_onboarding_and_remembered_account(self):
        self.app.device_state.save_profile({})
        self.app.device_state.save_setting(agreement=None)
        self.assertIn(b'Use and privacy agreement', self.client.get('/').data)
        accepted = self.client.post('/mobile/agree', json={'accepted': True}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(accepted.status_code, 200)
        self.assertIn(b'Sign in once', self.client.get('/').data)
        result = self.client.post('/mobile/login', json={'username': 'offline-test', 'password': 'test-password'},
                                  headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(result.status_code, 200, result.data)
        saved = (PROJECT / '.test-data/reader/desktop-session.json').read_text()
        self.assertNotIn('test-password', saved)
        self.assertEqual(self.app.device_state.profile['user_key'], PROFILE['user_key'])
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_support_reminder_waits_72_hours_and_persists_across_restarts(self):
        from offline_app import DeviceState, SUPPORT_REMINDER_SECONDS
        state=self.app.device_state
        old=dict(state.settings)
        try:
            state.save_setting(support_last_shown=0)
            self.assertEqual(self.client.post('/mobile/support/claim',json={}).status_code,400)
            def claim(moment):
                with patch.dict(self.app.config,SUPPORT_REMINDER_CLOCK=lambda:moment):
                    response=self.client.post('/mobile/support/claim',json={},headers={'X-CSRF-Token':self.csrf})
                    self.assertEqual(response.status_code,200)
                    return response.json['show']
            self.assertTrue(claim(1000000))
            self.assertFalse(claim(1000000))
            self.assertFalse(claim(1000000+SUPPORT_REMINDER_SECONDS-1))
            restored=DeviceState(state.root)
            self.assertEqual(restored.settings['support_last_shown'],1000000)
            self.assertTrue(claim(1000000+SUPPORT_REMINDER_SECONDS))
            self.assertFalse(FakeBridge.calls,'A support reminder must not call the website or Drive.')
        finally:
            with state.lock:
                state.settings.clear();state.settings.update(old);state.save_setting()

    def test_03_all_translations_and_word_study_offline(self):
        with patch('requests.post', side_effect=AssertionError('Offline reading made a network request')):
            translations = self.get('translations')['translations']
            self.assertEqual({item['code'] for item in translations if item['available']}, {'NIV','CEBOCB','TCB'})
            for code in ('NIV', 'CEBOCB', 'TCB'):
                verses = self.get('chapter?book=John&chapter=3&translation='+code)['verses']
                self.assertGreater(len(verses), 20)
                self.assertTrue(verses[15]['text'])
            chapter = self.get('chapter?book=John&chapter=3')['verses']
            self.get('word/'+str(chapter[15]['id'])+'/0')
            for source in ('mounce','vine','harper'):
                result = self.get('dictionaries/search?q=love&source='+source)
                self.assertTrue(result.get('entries') or result.get('results') or result.get('sources'), str(result)[:300])
            for code, word in [('TCB','Dios'),('CEBOCB','Dios')]:
                self.get('dictionaries/search?q='+word+'&translation='+code)
            self.get('concordance?q=love')

    def test_04_book_study_commentaries_sermons_illustrations(self):
        with patch('requests.post', side_effect=AssertionError('A bundled study contacted the network')):
            self.assertTrue(self.get('book-study?book=John')['available'])
            for series in ('wiersbe','exalting-jesus','moody','pulpit'):
                result = self.get('commentary?book=John&chapter=3&verse=16&series='+series)
                self.assertTrue(result.get('available'), (series, str(result)[:500]))
            sermons = self.get('sermons?book=John&chapter=3')['sermons']
            self.assertGreater(len(sermons), 0)
            self.assertTrue(self.get('sermons/'+sermons[0]['id'])['sermon']['blocks'])
            illustrations = self.get('illustrations/browse?letter=A')
            self.assertTrue(illustrations['available'])
            self.assertTrue(illustrations.get('entries') or illustrations.get('topics'))

    def test_05_marks_bookmarks_private_and_local_deletion(self):
        verse = self.get('chapter?book=John&chapter=3')['verses'][15]
        response = self.write('annotations', dict(kind='highlight', color='#fffeb3', ranges=[dict(
            verse_id=verse['id'], start_offset=0, end_offset=3, selected_text=verse['text'][:3])]))
        self.assertEqual(response.status_code, 201, response.data)
        mark = response.get_json()['annotation']['id']
        response = self.write('bookmarks', dict(verse_id=verse['id'], label='Offline bookmark'))
        self.assertEqual(response.status_code, 201, response.data)
        bookmark = response.get_json()['bookmark']['id']
        self.assertIn(b'Offline', self.client.get('/pastor-resources/my-library').data)
        self.assertIn(b'data-kind="bookmarks"', self.client.get('/pastor-resources/my-library').data)
        self.app.device_state.save_profile(dict(PROFILE, user_key='pastor:someone-else', username='someone-else'))
        self.assertEqual(self.get('personal')['annotations'], [])
        self.assertEqual(self.client.get(API+'personal/annotations/'+mark).status_code, 404)
        self.app.device_state.save_profile(PROFILE)
        self.assertEqual(self.write('personal/annotations/'+mark, {}, 'DELETE').status_code, 200)
        self.assertEqual(self.write('personal/bookmarks/'+bookmark, {}, 'DELETE').status_code, 200)
        self.assertEqual(self.client.get(API+'personal/annotations/'+mark).status_code, 404)

    def test_06_notes_rich_format_pdf_versions_and_delete(self):
        created = self.write('notes', {'note': DOCUMENT})
        self.assertEqual(created.status_code, 201, created.data)
        item = created.get_json()['item']; identifier = item['id']
        stored = self.get('notes/'+identifier)['item']
        run = next(run for block in stored['note']['content']['blocks'] for run in block['runs'] if 'Faith,' in run['text'])
        self.assertTrue(run['bold'] and run['italic'] and run['underline'])
        self.assertEqual(run['color'], '#123456')
        pdf = self.client.get(API+'notes/'+identifier+'/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b'%PDF-'), pdf.data[:100])
        self.assertEqual(self.write('notes/'+identifier, dict(note=DOCUMENT, version=0), 'PATCH').status_code, 409)
        self.app.device_state.save_profile(dict(PROFILE, user_key='pastor:another'))
        self.assertEqual(self.client.get(API+'notes/'+identifier).status_code, 404)
        self.app.device_state.save_profile(PROFILE)
        self.assertEqual(self.write('notes/'+identifier, {'version': item['version']}, 'DELETE').status_code, 200)
        self.assertEqual(self.client.get(API+'notes/'+identifier).status_code, 404)

    def test_07_offline_backup_keeps_notes_and_cannot_convert(self):
        created = self.write('notes', {'note': DOCUMENT}).get_json()['item']
        FakeBridge.connected = False
        result = self.write('notes/sync', {}).get_json()
        self.assertFalse(result['job']['running'])
        self.assertTrue(result['job']['error'])
        self.assertEqual(self.get('notes/'+created['id'])['item']['note']['theme'], DOCUMENT['theme'])
        self.assertEqual(self.write('notes/convert', {}).status_code, 405)
        self.app.device_state.save_profile(dict(PROFILE,can_convert=True))
        self.assertEqual(self.write('notes/convert', {}).status_code,405)
        self.assertFalse(self.get('notes')['can_convert'])

    def test_08_no_account_change_during_backup(self):
        import bible_study
        key = (self.app.config['BIBLE_SERMON_NOTES_DATABASE'], PROFILE['user_key'])
        bible_study._notes_jobs[key] = dict(running=True)
        try:
            result = self.client.post('/mobile/logout', json={}, headers={'X-CSRF-Token': self.csrf})
            self.assertEqual(result.status_code, 409)
            self.assertTrue(self.app.device_state.profile)
        finally: bible_study._notes_jobs[key] = dict(running=False)

    def test_09_profiles_and_no_token_in_html(self):
        html = self.client.get('/pastor-resources/bible-study').get_data(as_text=True)
        self.assertNotIn(PROFILE['token'], html)
        about = self.client.get('/about-developer').get_data(as_text=True)
        self.assertIn('Pijeme', about); self.assertIn('Bernard', about)
        self.assertNotIn('Mervin', about)

    def test_10_sync_pushes_then_applies_remote_deletion_locally(self):
        item = self.write('notes', {'note': DOCUMENT}).get_json()['item']
        result = self.write('notes/sync', {}).get_json()['job']
        self.assertFalse(result.get('error'), result)
        self.assertGreater(result['pushed'], 0)
        self.assertFalse(self.get('notes/'+item['id'])['item']['dirty'])
        self.assertEqual([path for path, _ in FakeBridge.calls].count('website-sync/start'), 2)
        record = FakeBridge.records[item['id']]
        record.update(version=record['version']+1, deleted=True, request_id='remote-deletion')
        record.pop('note', None)
        result = self.write('notes/sync', {}).get_json()['job']
        self.assertFalse(result.get('error'), result)
        self.assertGreater(result['pulled'], 0)
        self.assertEqual(self.client.get(API+'notes/'+item['id']).status_code, 404)


    def test_11_saved_study_anchors_reopen_and_delete(self):
        source = self.get('book-study?book=John')['study_anchors'][0]
        text = source['text']
        result = self.write('study-saves',dict(kind='note', note='Offline study annotation', ranges=[dict(
            token=source['token'], text=text, start_offset=0, end_offset=min(12,len(text)), selected_text=text[:12])]))
        self.assertEqual(result.status_code,201,result.data)
        item = result.get_json()['item']
        page = self.client.get('/pastor-resources/my-library').get_data(as_text=True)
        self.assertIn('Open study source',page)
        self.assertIn('study='+item['id'],page)
        self.assertEqual(self.write('study-saves/'+item['id'],{},'DELETE').status_code,200)


    def test_12_all_source_verified_spacing_corrections_are_readable_offline(self):
        update_spec=self.app.resource_manifest['bible_spacing_update']
        web=Path(self.app.static_folder).parent
        update_path=web/update_spec['file']
        self.assertEqual(file_hash(update_path),update_spec['sha256'])
        update=json.loads(update_path.read_text(encoding='utf-8'))
        self.assertEqual(update['scanned'],dict(NIV=31087,CEBOCB=30552,TCB=30868))
        self.assertEqual(len(update['changes']),8678)
        import bible_study
        examples={}
        with closing(sqlite3.connect(self.app.config['BIBLE_STUDY_DATABASE'])) as db:
            db.row_factory=sqlite3.Row
            for item in update['changes']:
                row=db.execute('''SELECT v.* FROM bible_verses v JOIN bible_editions e ON e.id=v.edition_id
                    WHERE e.translation=? AND e.is_active=1 AND v.is_active=1
                    AND v.book_id=? AND v.chapter=? AND v.verse=?''',
                    (item['translation'],item['book_id'],item['chapter'],item['verse'])).fetchone()
                self.assertIsNotNone(row,item)
                self.assertEqual(hashlib.sha256(row['text'].encode()).hexdigest(),item['after_sha256'])
                for token in db.execute('SELECT start_offset,end_offset,text FROM bible_tokens WHERE verse_id=?',(row['id'],)):
                    self.assertEqual(row['text'][token['start_offset']:token['end_offset']],token['text'])
                for span in db.execute('SELECT start_offset,end_offset FROM bible_red_letter_spans WHERE verse_id=?',(row['id'],)):
                    self.assertTrue(0<=span[0]<span[1]<=len(row['text']))
                examples.setdefault(item['translation'],dict(item,text=row['text']))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with patch('requests.post',side_effect=AssertionError('Corrected Bible reading must work offline')):
            for kind,item in examples.items():
                response=self.client.get(API+'chapter',query_string=dict(book=bible_study.BOOKS[item['book_id']-1][0],
                    chapter=item['chapter'],translation=kind))
                self.assertEqual(response.status_code,200,response.data)
                self.assertIn(item['text'],[v['text'] for v in response.get_json()['verses']])


@unittest.skipUnless(os.environ.get('IOWO_BROWSER_TESTS') == '1', 'Set IOWO_BROWSER_TESTS=1 for Chromium and WebKit UI checks.')
class BrowserTests(unittest.TestCase):
    def test_desktop_android_sized_and_iphone_sized_screens(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server, WSGIRequestHandler
        class QuietHandler(WSGIRequestHandler):
            def log(self, *args, **kwargs): pass
        app = prepared_app()
        app.device_state.save_profile(dict(PROFILE, can_convert=True))
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = 'http://127.0.0.1:'+str(server.server_port)
        screenshots = PROJECT / 'test-results'; screenshots.mkdir(exist_ok=True)
        try:
            with sync_playwright() as playwright:
                for engine, width, height in [('chromium',1280,900),('chromium',390,844),('webkit',390,844),('webkit',820,1180),('webkit',1180,820)]:
                    with self.subTest(engine=engine, width=width):
                        app.device_state.save_profile(dict(PROFILE, can_convert=True))
                        app.device_state.save_setting(support_last_shown=0)
                        # Each browser starts its own fictional reading position;
                        # section navigation in one scenario must not seed another.
                        with closing(sqlite3.connect(app.config['BIBLE_STUDY_DATABASE'])) as database:
                            database.execute('DELETE FROM bible_reader_state WHERE user_key=?',(PROFILE['user_key'],))
                            database.commit()
                        browser = getattr(playwright,engine).launch()
                        context = browser.new_context(viewport=dict(width=width,height=height), device_scale_factor=1,
                                                      is_mobile=width<600 or engine=='webkit', has_touch=width<600 or engine=='webkit',
                                                      **({'user_agent':'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'} if engine=='webkit' and width>700 else {}))
                        context.add_cookies([dict(name='iowo_device_gate',value='test-gate',url=url,httpOnly=True,sameSite='Strict')])
                        page = context.new_page(); errors=[]; failed=[]; external=[]
                        page.add_init_script("window.nativeExports=[];window.nativeTutorialOpen=0;window.nativeTutorialActive=true;window.IOWOAndroid={tutorialActive:()=>window.nativeTutorialActive,openTutorials:()=>window.nativeTutorialOpen++,exportFile:(...args)=>window.nativeExports.push(args),copyText:()=>{},shareText:()=>{}};")
                        page.add_init_script("""
                            window.studyFetchGate={match:'/api/chapter',waiting:false};
                            const originalFetch=window.fetch.bind(window);
                            window.fetch=async(...args)=>{
                                const gate=window.studyFetchGate;
                                if(gate.match&&String(args[0]).includes(gate.match)){
                                    gate.waiting=true;
                                    await new Promise(resolve=>{gate.release=resolve;});
                                }
                                return originalFetch(...args);
                            };
                        """)
                        page.on('pageerror',lambda error: errors.append(str(error)))
                        page.on('response',lambda response: failed.append((response.status,response.url)) if response.status>=400 else None)
                        def offline_route(route):
                            if route.request.url.startswith(url+'/'): route.continue_()
                            else: external.append(route.request.url); route.abort()
                        page.route('**/*',offline_route)
                        page.goto(url+'/pastor-resources/bible-study')
                        page.wait_for_function('window.studyFetchGate.waiting')
                        self.assertEqual(page.locator('#bsLoadingTitle').inner_text(),'Preparing Bible…')
                        self.assertTrue(page.locator('#bsLoadingProgress').is_visible())
                        self.assertIsNone(page.locator('#bsLoadingProgress').get_attribute('value'))
                        self.assertEqual(page.locator('#bsScripture').get_attribute('aria-busy'),'true')
                        self.assertNotIn('has not been indexed',page.locator('#bsEmpty').inner_text())
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-preparing-bible.png')))
                        page.evaluate('window.studyFetchGate.match=null;window.studyFetchGate.release()')
                        page.wait_for_function("document.querySelectorAll('#bsVerses .bs-verse').length > 5")
                        page.wait_for_function('window.iowoStudyReady===true || window.iowoStartupError',timeout=30000)
                        self.assertTrue(page.evaluate('window.iowoStudyReady===true'),page.evaluate('window.iowoStartupError'))
                        self.assertEqual(page.evaluate('window.iowoStartupProgress.value'),100)
                        self.assertTrue(page.evaluate("window.iowoStartupDurationMs>0"))
                        support=page.locator('#mobileSupportDialog')
                        self.assertTrue(support.is_hidden())
                        page.evaluate("window.nativeTutorialActive=false;window.dispatchEvent(new Event('iowo-tutorial-closed'))")
                        support.wait_for(state='visible')
                        self.assertIn('Completely voluntary',support.inner_text())
                        self.assertTrue(page.locator('#mobileSupportContinue').evaluate('(el)=>document.activeElement===el'))
                        self.assertTrue(page.locator('#mobileSupportDialog img').evaluate('(el)=>el.complete && el.naturalWidth>0'))
                        page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.supportClipboard=text;}}})")
                        page.locator('#mobileSupportCopy').click()
                        self.assertEqual(page.evaluate('window.supportClipboard'),'09171655575')
                        self.assertTrue(support.is_visible())
                        page.screenshot(path=str(screenshots/(engine+'-'+str(width)+'-support-reminder.png')))
                        page.locator('#mobileSupportContinue').click();support.wait_for(state='hidden')
                        # Resuming the same WebView after 72 hours also rechecks.
                        future=time.time()+3*24*60*60+10
                        app.config['SUPPORT_REMINDER_CLOCK']=lambda:future
                        page.evaluate("value=>{window.originalSupportNow=Date.now;Date.now=()=>value;document.dispatchEvent(new Event('visibilitychange'));}",future*1000)
                        support.wait_for(state='visible')
                        page.keyboard.press('Escape');support.wait_for(state='hidden')
                        page.evaluate('Date.now=window.originalSupportNow')
                        app.config.pop('SUPPORT_REMINDER_CLOCK',None)


                        self.assertTrue(page.locator('#bsNotesList button').count()>0)
                        self.assertEqual(page.locator('#bsNotesConvert').count(),0)

                        self.assertTrue(page.locator('#bsEmpty').is_hidden())
                        self.assertEqual(page.locator('#bsScripture').get_attribute('aria-busy'),'false')
                        page.evaluate('document.fonts.ready')
                        self.assertEqual(page.locator('.app-header,#mobileMenu,#mobileDrawer').count(),0)
                        self.assertLess(page.locator('#bibleStudyRoot').bounding_box()['y'],2)
                        self.assertIn('Lora',page.locator('#bsVerses .bs-word').first.evaluate('(el)=>getComputedStyle(el).fontFamily'))
                        self.assertIn('Rye',page.locator('.bs-handwritten').first.evaluate('(el)=>getComputedStyle(el).fontFamily'))
                        tab_positions=[]
                        for selector in ('#bsReaderTab','#bsBookStudyTab','#bsNotesTab','#bsWordTab','#bsConcordanceTab','#bsCommentaryTab','#bsSermonTab','#bsIllustrationsTab'):
                            tab_positions.append(page.locator(selector).evaluate('(el)=>getComputedStyle(el).backgroundPosition'))
                        self.assertEqual(len(set(tab_positions)),8)
                        self.assertLessEqual(page.locator('#bsWideTabs').bounding_box()['height'],34)
                        study_tabs=page.locator('[role=tablist][aria-label="Study tools"]')
                        self.assertLessEqual(study_tabs.bounding_box()['height'],34)
                        scene=page.locator('#bsWordPanel .bs-banner')
                        self.assertIn('study-wood-atlas',scene.evaluate('(el)=>getComputedStyle(el,"::before").backgroundImage'))
                        self.assertEqual(scene.evaluate('(el)=>getComputedStyle(el,"::after").maskImage'),'none')
                        self.assertLessEqual(scene.bounding_box()['height'],54)
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-reader.png')),full_page=True)
                        for tab,panel,suffix in [('#bsBookStudyTab','#bsBookStudyPanel','book-study'),
                            ('#bsConcordanceTab','#bsConcordancePanel','concordance'),
                            ('#bsCommentaryTab','#bsCommentaryPanel','commentary'),
                            ('#bsSermonTab','#bsSermonPanel','outlines'),
                            ('#bsIllustrationsTab','#bsIllustrationsPanel','illustrations')]:
                            request_fragment={'book-study':'/book-study?','concordance':'/concordance?',
                                'commentary':'/commentary?','outlines':'/sermons?',
                                'illustrations':'/illustrations/suggested?'}[suffix]
                            page.evaluate('(match)=>{window.studyFetchGate={match,waiting:false}}',request_fragment)
                            page.locator(tab).click()
                            page.locator(panel).wait_for(state='visible')
                            if suffix=='concordance':
                                page.locator('#bsConcordanceSearch').fill('God')
                                page.locator('#bsConcordanceForm button[type=submit]').click()
                            notice=page.locator(panel+' .bs-loading-notice')
                            if suffix=='concordance':
                                page.wait_for_function('window.studyFetchGate.waiting')
                                self.assertTrue(notice.is_visible())
                                self.assertTrue(notice.locator('progress').is_visible())
                                page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-preparing-'+suffix+'.png')))
                                page.evaluate('window.studyFetchGate.match=null;window.studyFetchGate.release()')
                            notice.wait_for(state='hidden')
                            if suffix!='concordance':
                                self.assertFalse(page.evaluate('window.studyFetchGate.waiting'),'Preloaded tool fetched again: '+suffix)
                                page.evaluate('window.studyFetchGate.match=null')
                            self.assertLessEqual(page.locator(panel+' .bs-banner').bounding_box()['height'],54)
                            page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-'+suffix+'.png')),full_page=True)
                        # Use real bundled chapters with 2, 4 and 9 sections,
                        # including themes that cannot fit in the inline strip.
                        page.locator('#bsCommentaryTab').click()
                        page.wait_for_function("document.querySelector('#bsCommentaryViewAll').textContent==='View all (2)'")
                        for choice in page.locator('#bsCommentaryChoices button').all():
                            self.assertLessEqual(choice.bounding_box()['height'],30)
                        for theme in page.locator('#bsCommentaryThemeList button').all():
                            self.assertLessEqual(theme.bounding_box()['height'],32)
                        page.locator('#bsCommentaryViewAll').click()
                        self.assertEqual(page.locator('#bsCommentarySectionsList button').count(),2)
                        first_title=page.locator('#bsCommentarySectionsList button strong').first.inner_text()
                        page.locator('#bsCommentarySectionsList button').first.click()
                        page.wait_for_function("document.querySelector('#bsCommentarySectionPosition').textContent==='Section 1 of 2'")
                        self.assertTrue(page.locator('#bsCommentaryPrevious').is_disabled())
                        self.assertEqual(page.locator('#bsCommentaryResults .bs-commentary-entry h4').first.inner_text(),first_title)
                        page.locator('#bsCommentaryNext').click()
                        page.wait_for_function("document.querySelector('#bsCommentarySectionPosition').textContent==='Section 2 of 2'")
                        self.assertTrue(page.locator('#bsCommentaryNext').is_disabled())
                        page.locator('#bsCommentaryPrevious').click()
                        page.wait_for_function("document.querySelector('#bsCommentarySectionPosition').textContent==='Section 1 of 2'")
                        for reference,total in [('John 3',4),('Matthew 5',9)]:
                            page.locator('#bsReaderTab').click()
                            page.locator('#bsNavigate').click()
                            page.locator('#bsReferenceInput').fill(reference)
                            page.locator('#bsDirectReference button').click()
                            page.locator('[data-commentary-series="exalting-jesus"]').click()
                            page.wait_for_function('(count)=>document.querySelector("#bsCommentaryViewAll").textContent===`View all (${count})`',arg=total)
                            page.locator('#bsCommentaryViewAll').click()
                            self.assertEqual(page.locator('#bsCommentarySectionsList button').count(),total)
                            self.assertIn(reference,page.locator('#bsCommentarySectionsReference').inner_text())
                            self.assertEqual(page.locator('#bsCommentarySectionsList button[aria-pressed=true]').count(),1)
                            last=page.locator('#bsCommentarySectionsList button').last
                            title=last.locator('strong').inner_text()
                            page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-commentary-sections-'+str(total)+'.png')))
                            last.click()
                            self.assertFalse(page.locator('#bsCommentarySectionsDialog').is_visible())
                            page.wait_for_function('(count)=>document.querySelector("#bsCommentarySectionPosition").textContent===`Section ${count} of ${count}`',arg=total)
                            self.assertTrue(page.locator('#bsCommentaryNext').is_disabled())
                            self.assertEqual(page.locator('#bsCommentaryResults .bs-commentary-entry h4').first.inner_text(),title)
                            theme_bounds=page.locator('#bsCommentaryThemes').bounding_box()
                            study_bounds=page.locator('#bsStudy').bounding_box()
                            self.assertGreaterEqual(theme_bounds['y'],study_bounds['y'])
                            self.assertLessEqual(theme_bounds['y']+theme_bounds['height'],study_bounds['y']+study_bounds['height'])
                            if total==4:
                                page.locator('#bsCommentaryViewAll').click()
                                page.keyboard.press('Escape')
                                self.assertFalse(page.locator('#bsCommentarySectionsDialog').is_visible())
                                page.locator('[data-commentary-series="wiersbe"]').click()
                                page.wait_for_function("document.querySelector('#bsCommentaryResults .bs-commentary-entry h4')?.textContent==='A Matter of Life and Death'")
                                self.assertTrue(page.locator('#bsCommentaryThemes').is_hidden())
                                self.assertTrue(page.locator('#bsCommentarySectionNav').is_hidden())
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-commentary-section-reader.png')))
                        page.locator('[data-commentary-series="moody"]').click()
                        page.locator('#bsCommentaryResults .bs-loading-notice').wait_for(state='hidden')
                        self.assertTrue(page.locator('#bsCommentaryThemes').is_hidden())
                        self.assertTrue(page.locator('#bsCommentarySectionNav').is_hidden())
                        page.locator('#bsWordTab').click()
                        page.evaluate("window.studyFetchGate={match:'/dictionaries/search?',waiting:false}")
                        page.locator('#bsSearch').fill('love')
                        page.locator('#bsSearchForm button[type=submit]').click()
                        page.wait_for_function('window.studyFetchGate.waiting')
                        self.assertIn('Uncovering meaning',page.locator('#bsWordResults .bs-loading-notice').inner_text())
                        page.evaluate('window.studyFetchGate.match=null;window.studyFetchGate.release()')
                        page.locator('#bsWordResults .bs-loading-notice').wait_for(state='hidden')
                        page.locator('#bsReaderTab').click()
                        page.locator('#bsSettings').click()
                        page.get_by_role('link',name='Tutorials & guided tour').click()
                        self.assertEqual(page.evaluate('window.nativeTutorialOpen'),1)
                        self.assertTrue(page.locator('#bsSettingsDialog').is_visible())
                        page.locator('#bsTheme').select_option('dark')
                        self.assertNotEqual(page.locator('#bsWordPanel .bs-banner h2').evaluate('(el)=>getComputedStyle(el).color'),'rgb(53, 44, 34)')
                        page.locator('#bsTheme').select_option('paper')
                        page.locator('#bsCloseSettings').click()
                        self.assertIn('English - NIV',page.locator('#bsTranslation').inner_text())
                        page.locator('#bsTranslation').select_option('TCB')
                        page.wait_for_function("document.querySelector('#bsBibleAttribution').textContent.includes('Tagalog')")
                        page.locator('#bsNotesTab').click()
                        page.locator('#bsNotesAll').click()
                        page.locator('#bsNotesActionsToggle').click()
                        page.locator('#bsNotesActionsMenu').wait_for(state='visible')
                        menu = page.locator('#bsNotesActionsMenu').bounding_box()
                        self.assertLess(menu['height'],300,menu)
                        self.assertGreaterEqual(menu['x'],0)
                        self.assertLessEqual(menu['x']+menu['width'],width+2)
                        for selector in ('#bsNotesExport','#bsNotesSync'):
                            box=page.locator(selector).bounding_box()
                            self.assertLess(box['height'],80,(selector,box))
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-notes.png')),full_page=True)
                        page.locator('#bsNotesActionsToggle').click()
                        page.locator('#bsNotesCreate').click()
                        page.locator('#bsNotesMetaText').fill('John 3:16')
                        theme = 'Browser '+engine+' '+str(width)
                        page.locator('#bsNotesMetaTheme').fill(theme)
                        page.locator('#bsNotesMetaSubmit').click()
                        page.locator('#bsNotesEditor').wait_for(state='visible')
                        page.locator('#bsNotesKeyboardMode').click()
                        page.locator('#bsNotesEditor').press('ControlOrMeta+End')
                        page.locator('#bsNotesEditor').press('Enter')
                        page.locator('#bsNotesEditor').press_sequentially('Saved through the mobile editor.')
                        page.wait_for_function("document.querySelector('#bsNotesStatus').textContent.includes('Saved locally')")
                        self.assertEqual(page.locator('#bsNotesToolsToggle,#bsNotesEditMeta').count(),0)
                        for action in ('Read','Save','Delete'):
                            self.assertTrue(page.locator('#bsNotes'+action).is_visible())
                        page.locator('[data-notes-edit-meta="theme"]').click()
                        self.assertTrue(page.locator('#bsNotesMetaTheme').evaluate('(el)=>el===document.activeElement'))
                        page.locator('#bsNotesMetaTheme').fill(theme+' edited')
                        page.locator('#bsNotesMetaSubmit').click()
                        page.wait_for_function("document.querySelector('#bsNotesTheme').textContent.endsWith(' edited')")
                        page.locator('[data-notes-edit-meta="text"]').click()
                        self.assertTrue(page.locator('#bsNotesMetaText').evaluate('(el)=>el===document.activeElement'))
                        page.locator('[data-notes-close="NotesMetaDialog"]').click()
                        page.evaluate("""()=>{const editor=document.querySelector('#bsNotesEditor');editor.focus();const range=document.createRange();range.selectNodeContents(editor.lastElementChild);getSelection().removeAllRanges();getSelection().addRange(range);document.dispatchEvent(new Event('selectionchange'));}""")
                        page.locator('#bsNotesTools').wait_for(state='visible')
                        self.assertTrue(page.locator('#bsNotesTools').evaluate("el=>el.classList.contains('bs-selection-toolbar')"))
                        self.assertEqual(page.locator('#bsNotesTools details,#bsNotesTools summary').count(),0)
                        self.assertTrue(page.locator('#bsNotesFont').is_visible())
                        self.assertEqual(page.locator('#bsNotesFontSize').evaluate('(el)=>el.tagName'),'SELECT')
                        self.assertEqual(page.locator('#bsNotesEditor').get_attribute('inputmode'),'none')
                        self.assertFalse(page.locator('#bsNotesEditor').evaluate('e=>e.isContentEditable'),'Selection must not activate typing')
                        self.assertEqual(page.locator('#bsNotesEditor').evaluate('(el)=>getComputedStyle(el).borderRadius'),'0px')
                        self.assertTrue(page.locator('#bsNotesEditor').evaluate("el=>{const s=getComputedStyle(el);return ['paddingTop','paddingRight','paddingBottom','marginTop','marginRight','marginBottom','marginLeft'].every(side=>parseFloat(s[side])===0)&&s.paddingLeft==='8px'}"))
                        self.assertAlmostEqual(page.locator('#bsNotesEditor').bounding_box()['width'],page.locator('#bsNotesPanel').bounding_box()['width'],delta=1)
                        if width>700:
                            self.assertEqual(page.locator('#bibleStudyRoot').get_attribute('data-fixed-notes-tools'),'true')
                            before=page.locator('.bs-notes-dock').bounding_box()
                            page.locator('#bsDivider').focus();page.keyboard.press('Shift+ArrowLeft')
                            after=page.locator('.bs-notes-dock').bounding_box()
                            self.assertAlmostEqual(before['width'],after['width'],delta=1)
                            self.assertAlmostEqual(before['y'],after['y'],delta=1)

                        self.assertEqual(page.locator('#bsNotesTools [data-notes-selection-command="hiliteColor"]').count(), 6)
                        self.assertEqual(page.locator('#bsNotesFormat [data-notes-command="underline"],#bsNotesFormat [data-notes-command="hiliteColor"]').count(),0)
                        self.assertTrue(page.locator('#bsNotesFontSize').evaluate("el=>el.compareDocumentPosition(document.querySelector('[data-notes-command=bold]')) & Node.DOCUMENT_POSITION_FOLLOWING"))
                        bounds = page.locator('#bsNotesTools').bounding_box()
                        dock = page.locator('.bs-notes-dock').bounding_box()
                        mode=page.locator('#bsNotesModeSwitch')
                        self.assertLessEqual(bounds['y']+bounds['height'],mode.bounding_box()['y'])
                        self.assertLessEqual(bounds['height'],165)
                        self.assertEqual(page.locator('#bsNotesEditor').get_attribute('contenteditable'),'false')
                        self.assertEqual(page.locator('#bsNotesFormatMode').get_attribute('aria-pressed'),'true')
                        self.assertTrue(page.locator('#bsNotesCreate').is_visible())
                        self.assertTrue(page.locator('#bsNotesAll').is_visible())
                        self.assertTrue(page.locator('#bsNotesSave').is_visible())
                        page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedSermon=text;}}})")
                        page.locator('#bsNotesCopySelection').click()
                        self.assertIn('Saved through the mobile editor.', page.evaluate('window.copiedSermon'))
                        page.locator('#bsNotesErase').click()
                        self.assertNotIn('Saved through the mobile editor.',page.locator('#bsNotesEditor').inner_text())
                        page.locator('#bsNotesUndo').click()
                        self.assertIn('Saved through the mobile editor.',page.locator('#bsNotesEditor').inner_text())
                        self.assertNotEqual(page.evaluate('document.activeElement.id'),'bsNotesEditor')

                        page.locator('[data-notes-selection-command="hiliteColor"][data-color="#ffdef3"]').click()
                        self.assertTrue(page.locator('#bsNotesEditor').evaluate("el=>[...el.querySelectorAll('[style]')].some(span=>span.style.backgroundColor==='rgb(255, 222, 243)')"))
                        page.locator('[data-notes-command="bold"]').click()
                        page.locator('[data-notes-selection-command="underline"]').click()
                        self.assertTrue(page.locator('#bsNotesEditor').evaluate("el=>!!el.querySelector('b,strong,[style*=bold]')"))
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-notes-selection.png')))
                        page.keyboard.press('Escape')
                        self.assertTrue(page.locator('#bsNotesTools').is_hidden())
                        page.locator('#bsNotesRead').click()
                        self.assertTrue(mode.is_hidden(),'Read Notes must hide the input choice')
                        self.assertEqual(page.locator('#bsNotesEditor').get_attribute('contenteditable'),'false')
                        page.evaluate("const e=document.querySelector('#bsNotesEditor'),r=document.createRange();r.selectNodeContents(e.lastElementChild);getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));")
                        page.locator('#bsNotesTools').wait_for(state='visible')
                        self.assertTrue(page.locator('#bsNotesErase').is_hidden())
                        page.locator('[data-notes-selection-command=underline]').click()
                        self.assertEqual(page.locator('#bsNotesEditor').get_attribute('contenteditable'),'false')
                        page.keyboard.press('Escape')
                        if width<600 or engine=='webkit':
                            self.assertTrue(page.locator('#bsWideTabs').is_hidden())
                            page.locator('#bsNotesBack').click()
                        else:page.locator('#bsNotesRead').click()
                        self.assertTrue(mode.is_visible())
                        self.assertTrue(page.locator('#bsNotesCreate').is_visible())
                        self.assertTrue(page.locator('#bsNotesAll').is_visible())
                        page.locator('#bsNotesSave').click()
                        page.locator('#bsNotesActionsToggle').click()
                        page.locator('#bsNotesExport').click()
                        page.locator('#bsNotesDownload').wait_for(state='visible')
                        page.wait_for_function("!document.querySelector('#bsNotesDownload').disabled")
                        page.locator('#bsNotesDownload').click()
                        page.wait_for_function('window.nativeExports.length === 1')
                        export = page.evaluate('window.nativeExports[0]')
                        self.assertTrue(export[0].startswith('JVBER'),export[:1])
                        self.assertEqual(export[2],'application/pdf')
                        self.assertFalse(export[3])
                        page.locator('[data-notes-close="NotesExportDialog"]').click()
                        page.locator('#bsNotesAll').click()
                        page.locator('#bsNotesList button').filter(has_text=theme).first.click()
                        self.assertIn('Saved through the mobile editor.',page.locator('#bsNotesEditor').inner_text())
                        page.locator('#bsReaderTab').click()
                        page.locator('#bsSettings').click()
                        page.get_by_role('link',name='All notes & highlights').click()
                        self.assertIn('My notes, marks & bookmarks',page.locator('h1').inner_text())
                        page.goto(url+'/mobile/splash')
                        status=page.locator('.mobile-launch-status').bounding_box();art=page.locator('.mobile-launch-art').bounding_box();actions=page.locator('.mobile-launch-actions').bounding_box()
                        self.assertEqual(page.locator('.mobile-launch-art').evaluate('(el)=>getComputedStyle(el).objectFit'),'contain')
                        self.assertGreaterEqual(art['height'],height-2)
                        self.assertGreaterEqual(page.locator('.mobile-launch').bounding_box()['height'],height-1)
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-splash.png')))
                        page.locator('#mobileTutorials').click()
                        page.wait_for_timeout(3100)
                        self.assertTrue(page.url.endswith('/mobile/splash'))
                        self.assertEqual(page.evaluate('window.nativeTutorialOpen'),1)
                        page.locator('#mobilePublisher').click()
                        page.get_by_text('Pijeme Franco Walawala',exact=False).first.wait_for()
                        page.get_by_text('Bernard Balansag',exact=False).first.wait_for()
                        page.wait_for_timeout(3200)
                        self.assertIn('/about-publisher',page.url)
                        self.assertIn('About Publisher',page.title())
                        app.device_state.save_profile({})
                        page.goto(url+'/')
                        page.locator('#loginForm').wait_for()
                        page.evaluate('document.fonts.ready')
                        self.assertEqual(page.locator('.app-header,#mobileMenu,#mobileDrawer').count(),0)
                        self.assertEqual(page.locator('.splash-container').count(),1)
                        self.assertEqual(page.get_by_role('button',name='Log In',exact=True).count(),1)
                        self.assertIn('login-landscape',page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundImage'))
                        self.assertTrue(page.locator('.splash-logo').get_attribute('src').endswith('/mobile/branding/icon.png'))
                        self.assertLessEqual(page.locator('#loginUsername').bounding_box()['x']+page.locator('#loginUsername').bounding_box()['width'],width)
                        page.screenshot(path=str(screenshots / (engine+'-'+str(width)+'-login.png')),full_page=True)
                        page.locator('#loginUsername').fill('offline-test')
                        page.locator('#loginPassword').fill('test-password')
                        page.get_by_role('button',name='Show password',exact=True).click()
                        self.assertEqual(page.locator('#loginPassword').get_attribute('type'),'text')
                        page.get_by_role('button',name='Hide password',exact=True).click()
                        self.assertEqual(page.locator('#loginPassword').get_attribute('type'),'password')
                        page.get_by_role('button',name='Log In',exact=True).click()
                        page.wait_for_url('**/pastor-resources/bible-study')
                        self.assertEqual(errors,[])
                        self.assertEqual(failed,[])
                        self.assertEqual(external,[])
                        context.close(); browser.close()
        finally: server.shutdown(); thread.join(timeout=5)


class WebsiteIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.accounts = sqlite3.connect(':memory:'); self.accounts.row_factory = sqlite3.Row
        self.accounts.execute('CREATE TABLE sheet_accounts_cache(username,password,position,name)')
        self.accounts.executemany('INSERT INTO sheet_accounts_cache VALUES (?,?,?,?)', [
            ('offline-test','correct','Pastor','Test'),('pijeme','correct','Area Overseer','Developer')])
        self.accounts.commit()
        self.calls = []
        self.jobs = {}
        study = SimpleNamespace(_notes_jobs=self.jobs, _notes_jobs_lock=threading.RLock(),
            sermon_notes_remote=lambda app, user, action, **payload: (self.calls.append((user,action,payload)) or dict(ok=True)),
            sync_sermon_notes=lambda app, user, progress: dict(message='Done'))
        owner = SimpleNamespace(get_db=lambda: self.accounts, sync_from_sheets_if_needed=lambda: None)
        app = Flask('website-tests'); app.secret_key = 'isolated-test-key'
        root = PROJECT / '.test-data/server'; root.mkdir(parents=True, exist_ok=True)
        self.auth_db = root / 'mobile_devices.db'
        with closing(sqlite3.connect(self.auth_db)) as db:
            db.executescript('DROP TABLE IF EXISTS devices; DROP TABLE IF EXISTS login_attempts;')
        app.config.update(BIBLE_MOBILE_AUTH_DATABASE=str(self.auth_db), BIBLE_SERMON_NOTES_DATABASE=str(root/'notes.db'), BIBLE_MOBILE_JOBS_INLINE=True)
        register_mobile_bible_routes(app, owner, study)
        self.client = app.test_client(); self.app = app

    def tearDown(self): self.accounts.close()

    def login(self, username='offline-test', password='correct'):
        return self.client.post('/api/v1/bible-mobile/login', json=dict(username=username,password=password))

    def test_tokens_are_hashed_scoped_and_revoked(self):
        response = self.login(); self.assertEqual(response.status_code, 200)
        profile = response.get_json()['profile']; token = profile['token']
        with closing(sqlite3.connect(self.auth_db)) as db:
            row = db.execute('SELECT token_hash,credential_hash FROM devices').fetchone()
            self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(row[1], 'correct')
        headers = {'Authorization':'Bearer '+token}
        endpoint = '/api/v1/bible-mobile/'
        self.assertEqual(self.client.post(endpoint+'drive',json={'action':'list','user_key':'pastor:other'},headers=headers).status_code,400)
        self.assertEqual(self.client.post(endpoint+'drive',json={'action':'list'},headers=headers).status_code,200)
        self.assertEqual(self.calls[0][0], 'pastor:offline-test')
        self.assertEqual(self.client.post(endpoint+'conversion/start',json={},headers=headers).status_code,404)
        developer=self.login(username='pijeme').get_json()['profile']
        self.assertFalse(developer['can_convert'])
        self.assertEqual(self.client.post(endpoint+'conversion/start',json={},headers={'Authorization':'Bearer '+developer['token']}).status_code,404)
        self.assertEqual(self.client.post(endpoint+'logout',json={},headers=headers).status_code,200)
        self.assertEqual(self.client.post(endpoint+'drive',json={'action':'list'},headers=headers).status_code,401)

    def test_password_change_revokes_online_access_and_login_throttles(self):
        token = self.login().get_json()['profile']['token']
        self.accounts.execute("UPDATE sheet_accounts_cache SET password='changed' WHERE username='offline-test'"); self.accounts.commit()
        self.assertEqual(self.client.post('/api/v1/bible-mobile/drive',json={'action':'list'},headers={'Authorization':'Bearer '+token}).status_code,401)
        for _ in range(8): self.assertEqual(self.login(password='wrong').status_code,401)
        self.assertEqual(self.login(password='wrong').status_code,429)

    def test_existing_website_backup_is_reused(self):
        token = self.login().get_json()['profile']['token']
        key = (self.app.config['BIBLE_SERMON_NOTES_DATABASE'], 'pastor:offline-test')
        self.jobs[key] = dict(running=True,kind='sync',message='Existing backup')
        result = self.client.post('/api/v1/bible-mobile/website-sync/start',json={},headers={'Authorization':'Bearer '+token})
        self.assertEqual(result.get_json()['job']['message'],'Existing backup')

    def test_role_prefix_matches_every_existing_website_account_type(self):
        for role in ('Pastor','Member','Area Overseer','AO','Sub Area Overseer','District Overseer','DO'):
            self.accounts.execute('UPDATE sheet_accounts_cache SET position=? WHERE username=?',(role,'offline-test'))
            self.accounts.commit()
            result = self.login().get_json()['profile']
            self.assertEqual(result['user_key'],role.lower()+':offline-test')
            self.assertFalse(result['can_convert'])


class ResourceAuditTests(unittest.TestCase):
    def test_android_dependency_elf_alignment(self):
        import struct
        roots = [PROJECT/'app/build/python/pip/debug', PROJECT/'app/build/intermediates/merged_native_libs/debug']
        files = sorted({path for root in roots for path in root.rglob('*.so')})
        if not files: self.skipTest('Compile Android native dependencies first.')
        for path in files:
            data=path.read_bytes()
            if data[:5] != b'\x7fELF\x02': continue
            endian='<' if data[5]==1 else '>'
            offset=struct.unpack_from(endian+'Q',data,32)[0]
            entrysize,count=struct.unpack_from(endian+'HH',data,54)
            for index in range(count):
                fields=struct.unpack_from(endian+'IIQQQQQQ',data,offset+index*entrysize)
                if fields[0]==1:
                    self.assertGreaterEqual(fields[-1],16384,(str(path),fields[-1]))

    def test_bundle_integrity_and_no_private_rows(self):
        manifest = json.loads((PROJECT / 'Resources/resource-manifest.json').read_text())
        self.assertEqual(sum(entry['bytes'] for entry in manifest['files']), manifest['resource_bytes'])
        for entry in manifest['files']:
            path = PROJECT / entry['project_path']
            self.assertEqual(path.stat().st_size, entry['bytes'], str(path))
            self.assertEqual(file_hash(path), entry['sha256'], str(path))
        path = PROJECT / 'Resources/study/bible_study.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                columns = {row[1] for row in db.execute('PRAGMA table_info("'+table+'")')}
                if 'user_key' in columns:
                    self.assertEqual(db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0], 0, table)
        self.assertFalse(list((PROJECT / 'app/src/main').rglob('.env*')))
        # Phone testing now has an explicitly authorized debug APK. Audit the
        # shared source assets independently of any existing build artifact.


class StartupAndSetupTests(unittest.TestCase):
    def test_remembered_origin_and_native_pdf_handoff(self):
        import offline_app
        class Native:
            session = json.dumps(PROFILE)
            def readSession(self): return self.session
            def writeSession(self,value): self.session=value
            def createPdf(self,value):
                self.document=json.loads(value)
                return b'%PDF-native-handoff'
        native=Native(); root=PROJECT/'.test-data/native-startup'
        root.mkdir(parents=True,exist_ok=True)
        arguments=(native,str(root),str(PROJECT/'Resources/runtime'),str(PROJECT/'Resources/resource-manifest.json'))
        urls=[]
        try:
            for _ in range(2):
                result=json.loads(offline_app.start_android(*arguments)); urls.append(result['url'])
                self.assertEqual(offline_app._application.device_state.profile['user_key'],PROFILE['user_key'])
                self.assertEqual(offline_app._core.sermon_notes_pdf(DOCUMENT),b'%PDF-native-handoff')
                self.assertEqual(native.document['primary_reference']['book_id'],43)
                self.assertFalse((root/'desktop-session.json').exists())
                offline_app._server.shutdown(); offline_app._server.server_close(); offline_app._server=None
            self.assertEqual(urls[0],urls[1])
        finally:
            if offline_app._server:
                offline_app._server.shutdown(); offline_app._server.server_close(); offline_app._server=None
            offline_app._application=None

    def test_setup_hash_validation_resume_and_preservation(self):
        root=PROJECT/'.test-data/setup-check'
        root.mkdir(parents=True,exist_ok=True)
        source=root/'public-source.txt'; source.write_text('Public offline fixture',encoding='utf-8')
        destination=root/'device/study/fixture.txt'
        if destination.exists(): destination.unlink()
        manifest=dict(resource_version='test',resource_bytes=source.stat().st_size,setup_free_bytes=source.stat().st_size+268435456,
                      files=[dict(path='study/fixture.txt',project_path=str(source.resolve()),bytes=source.stat().st_size,sha256=file_hash(source))])
        manifest_path=root/'manifest.json'; manifest_path.write_text(json.dumps(manifest))
        app=create_app(root/'device',PROJECT/'Resources/runtime',manifest_path,gate='test-gate',bridge_factory=FakeBridge)
        app.device_state.save_profile(PROFILE); app.device_state.save_setting(agreement=TERMS_VERSION,resources_ready=False)
        client=app.test_client(); client.set_cookie('iowo_device_gate','test-gate'); client.get('/')
        with client.session_transaction() as identity: csrf=identity['bible_study_csrf']
        def prepare():
            response=client.post('/mobile/setup',json={},headers={'X-CSRF-Token':csrf})
            self.assertEqual(response.status_code,200,response.data)
            for _ in range(200):
                state=client.get('/mobile/setup/status').get_json()
                if not state['running']: return state
                time.sleep(.01)
            self.fail('Fixture setup did not complete')
        self.assertTrue(prepare()['ready'])
        self.assertEqual(destination.read_bytes(),source.read_bytes())
        app.device_state.save_setting(resources_ready=False)
        self.assertTrue(prepare()['ready'])
        destination.write_text('A changed reader database must be kept',encoding='utf-8')
        app.device_state.save_setting(resources_ready=False)
        state=prepare(); self.assertFalse(state['ready']); self.assertIn('recovery',state['error'])
        self.assertIn('must be kept',destination.read_text())



@unittest.skipUnless(os.environ.get('IOWO_BROWSER_TESTS') == '1', 'Set IOWO_BROWSER_TESTS=1 for browser checks')
class TutorialTests(unittest.TestCase):
    def test_offline_screenshot_outline_tour_and_zoom(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server, WSGIRequestHandler
        class QuietHandler(WSGIRequestHandler):
            def log(self, *args, **kwargs): pass
        app=prepared_app()
        client=app.test_client()
        self.assertEqual(client.get('/mobile/tutorials/').status_code,403)
        client.set_cookie('iowo_device_gate','test-gate')
        # Help remains available before agreement, sign-in or library setup.
        app.device_state.save_profile(None)
        with client.get('/mobile/tutorials/') as response:self.assertEqual(response.status_code,200)
        self.assertEqual(client.get('/mobile/tutorials/../../settings.json').status_code,404)
        with client.get('/mobile/runtime/static/fonts/SpaceGrotesk-Variable.ttf') as response:self.assertEqual(response.status_code,200)
        app.device_state.save_profile(PROFILE)
        assets=PROJECT/'Resources/tutorials'
        manifest_source=(assets/'screenshots.js').read_text(encoding='utf-8')
        manifest=json.JSONDecoder().raw_decode(manifest_source[manifest_source.index('{'):])[0]
        files={item['file'] for item in manifest.values()}
        self.assertEqual(len(manifest),41)
        self.assertLess(sum((assets/name).stat().st_size for name in files),800_000)
        self.assertLessEqual(len(files),30)
        for name in files:
            self.assertLess((assets/name).stat().st_size,80_000,name)
            with client.get('/mobile/tutorials/'+name) as response:
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.mimetype,'image/webp')
        self.assertEqual(client.get('/mobile/tutorials/images/../../settings.json').status_code,404)
        with closing(sqlite3.connect(app.config['BIBLE_SERMON_NOTES_DATABASE'])) as db:
            before=db.execute('SELECT * FROM sermon_notes ORDER BY user_key,id').fetchall()
        server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        url='http://127.0.0.1:'+str(server.server_port)
        screenshots=PROJECT/'test-results';screenshots.mkdir(exist_ok=True)
        try:
            with sync_playwright() as playwright:
                for engine,width in [('chromium',1280),('chromium',390),('webkit',390)]:
                    with self.subTest(engine=engine,width=width):
                        browser=getattr(playwright,engine).launch()
                        context=browser.new_context(viewport=dict(width=width,height=844),is_mobile=width<600,has_touch=width<600)
                        context.add_cookies([dict(name='iowo_device_gate',value='test-gate',url=url)])
                        page=context.new_page();errors=[];unexpected=[];failed=[]
                        page.add_init_script("window.tutorialClosed=0;window.IOWOTutorial={finish:()=>window.tutorialClosed++,copyText:text=>window.demoClipboard=text};")
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('response',lambda response:failed.append((response.status,response.url)) if response.status>=400 else None)
                        def offline(route):
                            if route.request.url.startswith(url+'/mobile/'):
                                route.continue_()
                            else:
                                unexpected.append(route.request.url);route.abort()
                        page.route('**/*',offline)
                        page.goto(url+'/mobile/tutorials/?tour=1')
                        page.wait_for_function('window.iowoTutorialReady===true')
                        self.assertEqual(page.locator('#lessonTitle').inner_text(),'Open a Bible passage')
                        self.assertEqual(page.locator('#tourPosition').inner_text(),'1 of 8')
                        page.wait_for_function("document.querySelector('#screenshotCanvas img').complete && document.querySelector('#screenshotCanvas img').naturalWidth>0")
                        self.assertEqual(page.locator('#screenshotLabels li').count(),3)
                        self.assertEqual(page.locator('#screenshotCanvas svg text').all_text_contents(),['1','2','3'])
                        self.assertEqual(page.locator('[contenteditable],#practice').count(),0)
                        # Exactly the current topic's image is loaded, rather
                        # than eagerly decoding every image in the outline.
                        self.assertEqual(page.evaluate("performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.webp')).length"),1)
                        page.locator('#nextLesson').click()
                        self.assertEqual(page.locator('#lessonTitle').inner_text(),'Thompson Chain Index')
                        page.locator('#enlargeScreenshot').click()
                        self.assertTrue(page.locator('#screenshotZoom').is_visible())
                        self.assertEqual(page.locator('#zoomLabels li').count(),3)
                        page.locator('#zoomToggle').click()
                        self.assertEqual(page.locator('#zoomToggle').get_attribute('aria-pressed'),'true')
                        self.assertTrue(page.locator('#zoomScroll').evaluate('(el)=>el.scrollWidth>el.clientWidth') if width<600 else True)
                        self.assertTrue(page.evaluate('window.iowoTutorialBack()'))
                        self.assertFalse(page.locator('#screenshotZoom').is_visible())
                        for _ in range(6):page.locator('#nextLesson').click()
                        self.assertEqual(page.locator('#tourPosition').inner_text(),'8 of 8')
                        self.assertIn('Sync Notes',page.locator('#screenshotLabels').inner_text())
                        page.locator('#nextLesson').click();self.assertEqual(page.evaluate('window.tutorialClosed'),1)
                        page.goto(url+'/mobile/tutorials/')
                        page.wait_for_function('window.iowoTutorialReady===true')
                        count=page.locator('#tutorialOutline button[data-lesson]').count();self.assertGreaterEqual(count,40)
                        self.assertEqual(page.locator('#tutorialOutline details').count(),5)
                        self.assertTrue(page.locator('#lessonPanel').is_hidden())
                        page.locator('#tutorialSearch').fill('zzzz-unmatched')
                        self.assertTrue(page.locator('#noResults').is_visible())
                        page.locator('#tutorialSearch').fill('highlight')
                        self.assertGreater(page.locator('#tutorialOutline button[data-lesson]').count(),0)
                        page.locator('#tutorialSearch').fill('')
                        page.locator('#quickLinks').get_by_role('button',name='Highlight in Sermon Notes').click()
                        self.assertIn('Highlight',page.locator('#lessonTitle').inner_text())
                        page.wait_for_function("document.querySelector('#screenshotCanvas img').complete && document.querySelector('#screenshotCanvas img').naturalWidth>0")
                        self.assertIn('highlight colour',page.locator('#screenshotLabels').inner_text())
                        self.assertEqual(page.locator('#screenshotCanvas svg text').count(),3)
                        page.locator('#enlargeScreenshot').click()
                        page.locator('#zoomToggle').click()
                        page.screenshot(path=str(screenshots/(engine+'-'+str(width)+'-tutorial-zoom.png')))
                        page.keyboard.press('Escape');self.assertFalse(page.locator('#screenshotZoom').is_visible())
                        self.assertTrue(page.locator('#enlargeScreenshot').evaluate('(el)=>document.activeElement===el'))
                        page.locator('#markReviewed').click();self.assertEqual(page.locator('#markReviewed').inner_text(),'Reviewed')
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                        page.screenshot(path=str(screenshots/(engine+'-'+str(width)+'-tutorial-highlight.png')))
                        page.reload();page.wait_for_function('window.iowoTutorialReady===true')
                        self.assertEqual(page.locator('#markReviewed').inner_text(),'Reviewed')
                        page.evaluate("location.hash='thompson-chain'")
                        page.wait_for_function("document.querySelector('#lessonTitle').textContent==='Thompson Chain Index'")
                        page.evaluate("location.hash='notes-highlighting'")
                        page.wait_for_function("document.querySelector('#lessonTitle').textContent==='Highlight in Sermon Notes'")
                        self.assertTrue(page.evaluate('window.iowoTutorialBack()'))
                        self.assertFalse(page.evaluate('window.iowoTutorialBack()'))
                        page.screenshot(path=str(screenshots/(engine+'-'+str(width)+'-tutorial-outline.png')))
                        # Every topic has a decoded screenshot, arrows and labels;
                        # viewing help never calls study/account APIs.
                        ids=page.locator('#tutorialOutline button[data-lesson]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.lesson)')
                        for lesson in ids:
                            page.locator('#tutorialOutline button[data-lesson="'+lesson+'"]').click()
                            self.assertGreater(len(page.locator('#lessonSteps li').all_text_contents()),1)
                            page.wait_for_function("document.querySelector('#screenshotCanvas img').complete && document.querySelector('#screenshotCanvas img').naturalWidth>0")
                            self.assertGreater(page.locator('#screenshotLabels li').count(),0,lesson)
                            self.assertEqual(page.locator('#screenshotCanvas svg text').count(),page.locator('#screenshotLabels li').count())
                            self.assertTrue(page.locator('#screenshotError').is_hidden(),lesson)
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'),lesson)
                            page.locator('#backToOutline').click()
                        self.assertFalse(errors,errors);self.assertFalse(failed,failed);self.assertFalse(unexpected,unexpected)
                        browser.close()
            with closing(sqlite3.connect(app.config['BIBLE_SERMON_NOTES_DATABASE'])) as db:
                self.assertEqual(db.execute('SELECT * FROM sermon_notes ORDER BY user_key,id').fetchall(),before)
        finally:server.shutdown()

class IOSBridgeTests(unittest.TestCase):
    def test_native_service_contract_and_pdf_bytes(self):
        import base64
        import ios_bridge
        calls=[]
        def call(method,payload):
            value=json.loads(payload);calls.append((method,value))
            result={'session.read':'{"user_key":"pastor:fixture"}',
                    'resources.progress':{'completed':1,'total':32},
                    'resources.required':2_162_016_256,
                    'pdf.create':base64.b64encode(b'%PDF-fixture').decode()}.get(method)
            return json.dumps({'ok':True,'result':result})
        with patch.dict(sys.modules,{'_iowo_native':SimpleNamespace(call=call)}):
            native=ios_bridge.NativeServices()
            self.assertEqual(json.loads(native.readSession())['user_key'],'pastor:fixture')
            native.writeSession('{"user_key":"pastor:fixture"}')
            self.assertEqual(native.requiredSetupBytes(),2_162_016_256)
            self.assertEqual(json.loads(native.resourceProgress())['total'],32)
            self.assertEqual(native.createPdf('{}'),b'%PDF-fixture')
            native.installResources();native.beginSync('Saving')
            self.assertIn(('session.write','{"user_key":"pastor:fixture"}'),calls)

    def test_native_failures_are_reported_without_plaintext_session_fallback(self):
        import ios_bridge
        fake=SimpleNamespace(call=lambda *args:json.dumps({'ok':False,'error':'Device is locked'}))
        with patch.dict(sys.modules,{'_iowo_native':fake}):
            with self.assertRaisesRegex(RuntimeError,'Device is locked'):ios_bridge.NativeServices().readSession()

    def test_sqlite_preflight_requires_real_search_and_json_functions(self):
        import ios_bridge
        ios_bridge.preflight()


class BibleSpacingMaintenanceTests(unittest.TestCase):
    def setUp(self):
        from offline_app import load_core
        study=load_core(SimpleNamespace())
        self.study=study
        FakeBridge.calls.clear()
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'bible.db'
        study.initialize_database(self.path)
        self.plan=dict(schema_version=1,source_hashes={},changes=[],scanned={kind:1 for kind in ('NIV','CEBOCB','TCB')})
        with closing(sqlite3.connect(self.path)) as db, db:
            for index,kind in enumerate(('NIV','CEBOCB','TCB'),1):
                digest=hashlib.sha256(kind.encode()).hexdigest();self.plan['source_hashes'][kind]=digest
                db.execute("INSERT INTO bible_sources(id,title,sha256,source_path,created_at) VALUES(?,? ,?,'synthetic','now')",(index,'Synthetic '+kind,digest))
                db.execute("INSERT INTO bible_import_runs(id,source_id,status,started_at) VALUES(?,?,'validated','now')",(index,index))
                db.execute("INSERT INTO bible_editions(id,source_id,import_run_id,edition_key,translation,title,status,is_active) VALUES(?,?,?, ?,?,'Synthetic','ready',1)",(index,index,index,kind+':'+digest,kind))
                before='AlphaBeta gamma.';after='Alpha Beta gamma.'
                db.execute('INSERT INTO bible_verses(id,edition_id,book_id,chapter,verse,text,content_hash) VALUES(?,?,1,1,1,?,?)',(index,index,before,hashlib.sha256(before.encode()).hexdigest()))
                for number,match in enumerate(re.finditer(r'\w+',before)):
                    db.execute('INSERT INTO bible_tokens(verse_id,token_index,start_offset,end_offset,text) VALUES(?,?,?,?,?)',(index,number,match.start(),match.end(),match[0]))
                self.plan['changes'].append(dict(translation=kind,verse_id=index,book_id=1,chapter=1,verse=1,before=before,after=after,source_sha256=digest))
            db.execute("INSERT INTO bible_annotations VALUES('mark','member:one',1,'group','highlight',5,9,'Beta','yellow','Keep my note',1,'old','old')")
            db.execute("INSERT INTO bible_annotations VALUES('joined','member:two',1,'group2','note',0,9,'AlphaBeta','','Keep this private text',1,'old','old')")
            db.execute("INSERT INTO bible_bookmarks VALUES('bookmark','member:one',1,5,'My label','old','old')")
            db.execute("INSERT INTO bible_navigation_history VALUES(1,'member:one',1,'old')")
            db.execute("INSERT INTO bible_reader_state(user_key,edition_id,book_id,chapter,verse,character_offset,updated_at) VALUES('member:one',1,1,1,1,5,'old')")
            db.execute("INSERT INTO bible_red_letter_spans(verse_id,start_offset,end_offset,speaker,evidence_source_id,evidence_locator) VALUES(1,0,9,'Jesus',1,'Synthetic source')")
            db.execute("INSERT INTO bible_chain_topics VALUES(1,1,'topic','Synthetic topic','[]',1)")
            db.execute('INSERT INTO bible_chain_verses VALUES(1,1)')

    def test_usfm_closing_styles_preserve_explicit_source_spaces(self):
        plain=self.study._usfm_plain
        self.assertEqual(plain(r'\nd Alpha\nd* Beta'),'Alpha Beta')
        self.assertEqual(plain(r'Alpha \add Beta\add* gamma'),'Alpha Beta gamma')
        self.assertEqual(plain(r'Al\it pha\it*Beta'),'AlphaBeta')
        self.assertEqual(plain(r'\add Alpha \+it Beta\+it* gamma\add* delta'),'Alpha Beta gamma delta')

    def test_dry_run_never_modifies_live_data(self):
        before=self.path.read_bytes()
        report=self.study.repair_bible_spacing(self.path,self.plan,dry_run=True)
        self.assertEqual(report['status'],'validated')
        self.assertEqual(len(report['changes']),3)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse((self.path.parent/'backups').exists())

    def test_revisions_tokens_colors_and_personal_anchors_are_preserved(self):
        report=self.study.repair_bible_spacing(self.path,self.plan)
        self.assertEqual(report['status'],'completed')
        self.assertTrue(Path(report['backup']).is_file())
        self.assertEqual(report['anchors'],dict(annotations=2,bookmarks=1,history=1,reader_positions=1,deferred=0))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory=sqlite3.Row
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bible_verses WHERE is_active=1').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bible_verses').fetchone()[0],6)
            for change in report['changes']:
                old=db.execute('SELECT * FROM bible_verses WHERE id=?',(change['verse_id'],)).fetchone()
                new=db.execute('SELECT * FROM bible_verses WHERE id=?',(change['new_verse_id'],)).fetchone()
                self.assertEqual((old['text'],old['is_active'],old['revision']),('AlphaBeta gamma.',0,1))
                self.assertEqual((new['text'],new['is_active'],new['revision']),('Alpha Beta gamma.',1,2))
                self.assertEqual(new['content_hash'],hashlib.sha256(new['text'].encode()).hexdigest())
                for token in db.execute('SELECT * FROM bible_tokens WHERE verse_id=?',(new['id'],)):
                    self.assertEqual(new['text'][token['start_offset']:token['end_offset']],token['text'])
            new_id=report['changes'][0]['new_verse_id']
            annotation=db.execute("SELECT * FROM bible_annotations WHERE id='mark'").fetchone()
            self.assertEqual((annotation['verse_id'],annotation['start_offset'],annotation['end_offset'],annotation['selected_text'],annotation['note']),
                             (new_id,6,10,'Beta','Keep my note'))
            joined=db.execute("SELECT selected_text,note FROM bible_annotations WHERE id='joined'").fetchone()
            self.assertEqual(tuple(joined),('Alpha Beta','Keep this private text'))
            self.assertEqual(tuple(db.execute("SELECT verse_id,character_offset,label FROM bible_bookmarks").fetchone()),(new_id,6,'My label'))
            self.assertEqual(db.execute('SELECT verse_id FROM bible_navigation_history').fetchone()[0],new_id)
            self.assertEqual(db.execute('SELECT character_offset FROM bible_reader_state').fetchone()[0],6)
            self.assertEqual(tuple(db.execute('SELECT start_offset,end_offset FROM bible_red_letter_spans WHERE verse_id=?',(new_id,)).fetchone()),(0,10))
            self.assertEqual(db.execute('SELECT topic_id FROM bible_chain_verses WHERE verse_id=?',(new_id,)).fetchone()[0],1)
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())
        with closing(sqlite3.connect(report['backup'])) as db:
            self.assertEqual(db.execute('SELECT text FROM bible_verses WHERE id=1').fetchone()[0],'AlphaBeta gamma.')

    def test_letters_punctuation_and_existing_whitespace_cannot_be_changed(self):
        for after in ('Alpha Beta delta.','Alpha Beta gamma!','AlphaBeta gamma.','AlphaBeta\ngamma.'):
            plan=copy.deepcopy(self.plan);plan['changes'][0]['after']=after
            before=self.path.read_bytes()
            with self.assertRaises(self.study.SyncError):self.study.repair_bible_spacing(self.path,plan)
            self.assertEqual(self.path.read_bytes(),before)

    def test_stale_source_or_verse_rolls_back_every_correction(self):
        for alteration in ('source','verse'):
            plan=copy.deepcopy(self.plan)
            if alteration=='source':plan['source_hashes']['NIV']='f'*64
            else:plan['changes'][-1]['before']='Different original words'
            before=self.path.read_bytes()
            with self.assertRaises(self.study.SyncError):self.study.repair_bible_spacing(self.path,plan)
            self.assertEqual(self.path.read_bytes(),before)

    def test_reapplying_reviewed_plan_is_idempotent(self):
        self.study.repair_bible_spacing(self.path,self.plan)
        again=self.study.repair_bible_spacing(self.path,self.plan)
        self.assertEqual(again['status'],'unchanged')
        self.assertEqual(again['already_fixed'],3)
        self.assertFalse(again['changes'])

    def test_corrupt_saved_selection_is_retained_instead_of_misplaced(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE bible_annotations SET selected_text='Original quotation' WHERE id='mark'")
        report=self.study.repair_bible_spacing(self.path,self.plan)
        self.assertEqual(report['anchors']['deferred'],1)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(tuple(db.execute("SELECT verse_id,selected_text,note FROM bible_annotations WHERE id='mark'").fetchone()),(1,'Original quotation','Keep my note'))


    def compact_update(self, plan=None):
        plan = plan or self.plan
        update = dict(schema_version=1, source_hashes=plan['source_hashes'], changes=[], scanned=plan['scanned'])
        for change in plan['changes']:
            left, right, _ = self.study._spacing_offset_maps(change['before'], change['after'])
            update['changes'].append(dict(translation=change['translation'], book_id=change['book_id'],
                chapter=change['chapter'], verse=change['verse'],
                before_sha256=hashlib.sha256(change['before'].encode()).hexdigest(),
                after_sha256=hashlib.sha256(change['after'].encode()).hexdigest(),
                insertions=[i for i in range(len(left)) if right[i] != left[i]]))
        path=self.path.parent/'spacing.json'
        path.write_text(json.dumps(update),encoding='utf-8')
        return path, file_hash(path), update

    def test_compact_bundle_maps_saved_data_and_runs_only_once(self):
        from offline_app import apply_bundled_spacing_update
        path,digest,_=self.compact_update()
        with patch('requests.post',side_effect=AssertionError('Spacing repair must work offline')):
            report=apply_bundled_spacing_update(self.study,self.path,path,digest)
        self.assertEqual(report['anchors']['annotations'],2)
        self.assertEqual(report['anchors']['bookmarks'],1)
        self.assertEqual(len(report['changes']),3)
        self.assertTrue(Path(report['backup']).is_file())
        with patch.object(self.study,'repair_bible_spacing',side_effect=AssertionError('Update repeated')):
            self.assertEqual(apply_bundled_spacing_update(self.study,self.path,path,digest)['status'],'unchanged')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT selected_text,note FROM bible_annotations WHERE id='joined'").fetchone(),
                             ('Alpha Beta','Keep this private text'))

    def test_compact_bundle_rejects_unverified_changes_without_writes(self):
        from offline_app import apply_bundled_spacing_update
        path,digest,original=self.compact_update()
        for variant in ('bundle_hash','source_hash','verse_hash','after_hash','offset','duplicate'):
            update=copy.deepcopy(original)
            if variant=='source_hash':update['source_hashes']['NIV']='f'*64
            elif variant=='verse_hash':update['changes'][-1]['before_sha256']='f'*64
            elif variant=='after_hash':update['changes'][-1]['after_sha256']='f'*64
            elif variant=='offset':update['changes'][-1]['insertions']=[-1]
            elif variant=='duplicate':update['changes'].append(update['changes'][0])
            path.write_text(json.dumps(update),encoding='utf-8')
            before=self.path.read_bytes()
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                apply_bundled_spacing_update(self.study,self.path,path,'f'*64 if variant=='bundle_hash' else file_hash(path))
            self.assertEqual(self.path.read_bytes(),before)
            self.assertFalse((self.path.parent/'backups').exists())

    def test_compact_bundle_recovers_after_repair_committed_before_marker(self):
        from offline_app import apply_bundled_spacing_update
        path,digest,_=self.compact_update()
        self.study.repair_bible_spacing(self.path,self.plan)
        with patch.object(self.study,'repair_bible_spacing',side_effect=AssertionError('Already corrected text reapplied')):
            self.assertEqual(apply_bundled_spacing_update(self.study,self.path,path,digest)['status'],'unchanged')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT value FROM bible_metadata WHERE key='bundled_spacing_update'").fetchone()[0],digest)
            self.assertEqual(db.execute('SELECT count(*) FROM bible_verses').fetchone()[0],6)

    def test_existing_installation_updates_during_startup(self):
        path,digest,_=self.compact_update()
        root=self.path.parent/'existing-device'
        (root/'study').mkdir(parents=True)
        target=root/'study/bible_study.db';shutil.copyfile(self.path,target)
        manifest=self.path.parent/'manifest.json'
        manifest.write_text(json.dumps(dict(resource_version='test',files=[],resource_bytes=0,
            bible_spacing_update=dict(file=path.name,sha256=digest))))
        app=create_app(root,path.parent,manifest,gate='test-gate',bridge_factory=FakeBridge)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT text FROM bible_verses WHERE is_active=1').fetchall(),[('Alpha Beta gamma.',)]*3)
        with patch.object(self.study,'repair_bible_spacing',side_effect=AssertionError('Startup reapplied repair')):
            create_app(root,path.parent,manifest,gate='test-gate',bridge_factory=FakeBridge)
        self.assertFalse(FakeBridge.calls)

    def test_fresh_setup_repairs_and_retries_without_replacing_reader(self):
        path,digest,_=self.compact_update()
        root=self.path.parent/'new-device'
        manifest=self.path.parent/'manifest.json'
        manifest.write_text(json.dumps(dict(resource_version='test',files=[],resource_bytes=0,
            bible_spacing_update=dict(file=path.name,sha256=digest))))
        class Native:
            copies=0
            def readSession(native):return json.dumps(PROFILE)
            def writeSession(native,value):pass
            def requiredSetupBytes(native):return 0
            def resourceProgress(native):return '{}'
            def installResources(native):
                native.copies+=1
                destination=root/'study/bible_study.db';destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.path,destination)
        native=Native()
        app=create_app(root,path.parent,manifest,native=native,gate='test-gate',bridge_factory=FakeBridge)
        app.device_state.save_setting(agreement=TERMS_VERSION)
        client=app.test_client();client.set_cookie('iowo_device_gate','test-gate');client.get('/mobile/setup/status')
        with client.session_transaction() as identity:csrf=identity['bible_study_csrf']
        save=app.device_state.save_setting
        def interrupted_save(**values):
            if values.get('resources_ready'):raise OSError('Simulated interruption after the repair commit')
            return save(**values)
        def prepare():
            result=client.post('/mobile/setup',json={},headers={'X-CSRF-Token':csrf})
            self.assertEqual(result.status_code,200,result.data)
            for _ in range(1000):
                state=client.get('/mobile/setup/status').get_json()
                if not state['running']:return state
                time.sleep(.01)
            self.fail('Fixture setup did not complete')
        with patch.object(app.device_state,'save_setting',side_effect=interrupted_save):
            first=prepare()
        self.assertFalse(first['ready']);self.assertIn('Simulated interruption',first['error'])
        self.assertEqual(app.device_state.settings['spacing_setup_pending'],digest)
        second=prepare();self.assertTrue(second['ready'],second)
        self.assertEqual(native.copies,1)
        with closing(sqlite3.connect(root/'study/bible_study.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bible_verses').fetchone()[0],6)
            self.assertEqual(db.execute("SELECT note FROM bible_annotations WHERE id='joined'").fetchone()[0],'Keep this private text')

if __name__ == '__main__': unittest.main(verbosity=2)
