"""iOS host adapter; the shared offline study engine remains unchanged."""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3


class NativeServices:
    def call(self, method, value=None):
        import _iowo_native
        reply = json.loads(_iowo_native.call(method, json.dumps(value)))
        if not reply.get('ok'):
            raise RuntimeError(reply.get('error', 'The device could not complete this request.'))
        return reply.get('result')

    def readSession(self): return self.call('session.read') or ''
    def writeSession(self, value): self.call('session.write', value)
    def installResources(self): self.call('resources.install')
    def resourceProgress(self): return json.dumps(self.call('resources.progress'))
    def requiredSetupBytes(self): return self.call('resources.required')
    def createPdf(self, value): return base64.b64decode(self.call('pdf.create', value), validate=True)
    def beginSync(self, message): self.call('sync.begin', message)


def preflight():
    # Check the actual device SQLite before opening any user's database.
    with closing(sqlite3.connect(':memory:')) as database:
        assert database.execute("SELECT json_extract('{\"ok\":1}', '$.ok')").fetchone()[0] == 1
        database.execute('CREATE VIRTUAL TABLE search_check USING fts5(text)')
        database.execute("INSERT INTO search_check VALUES ('study the Word')")
        assert database.execute("SELECT count(*) FROM search_check WHERE search_check MATCH 'Word'").fetchone()[0] == 1


def start(data_directory, resources_directory):
    preflight()
    import offline_app
    resources = Path(resources_directory)
    return offline_app.start_android(NativeServices(), data_directory,
                                     str(resources / 'runtime'), str(resources / 'resource-manifest.json'))


def sync_status():
    import offline_app
    return offline_app.sync_status()
