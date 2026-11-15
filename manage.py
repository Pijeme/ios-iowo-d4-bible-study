"""Verify the standalone iOS sources or run the shared UI on Windows/macOS."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def verify():
    manifest = json.loads((ROOT / 'Resources/resource-manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        path = ROOT / entry['project_path']
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        assert digest == entry['sha256'] and path.stat().st_size == entry['bytes'], entry['path']
    spacing = manifest['bible_spacing_update']
    update = ROOT / 'Resources/runtime' / spacing['file']
    assert hashlib.sha256(update.read_bytes()).hexdigest() == spacing['sha256'], spacing['file']
    packages = ROOT / 'Python/site-packages'
    bad = [p for p in packages.rglob('*') if p.suffix.lower() in ('.so', '.pyd', '.dll', '.dylib')]
    assert not bad, bad
    sys.path.insert(0, str(packages))
    import markupsafe
    assert markupsafe.escape('<Bible>') == '&lt;Bible&gt;'
    print('Verified', len(manifest['files']), 'offline resources and pure-Python package loading.')


def restore_library(archive_path):
    """Restore only manifest-listed reference files from a local APK or handoff ZIP."""
    import os
    import tempfile
    import zipfile
    manifest = json.loads((ROOT / 'Resources/resource-manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        for entry in manifest['files']:
            destination = (ROOT / entry['project_path']).resolve()
            if not destination.is_relative_to(ROOT.resolve() / 'Resources'):
                raise ValueError('Invalid resource destination.')
            if destination.exists():
                with destination.open('rb') as stream:
                    valid = hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
                if valid and destination.stat().st_size == entry['bytes']:
                    continue
                raise ValueError(f"Existing resource differs; preserve it and review: {entry['project_path']}")
            member = next((name for name in [entry['project_path'], 'assets/' + entry['path']] if name in names), None)
            if member is None:
                raise ValueError(f"Archive is missing {entry['path']}; use the matching standalone APK or full iOS handoff ZIP.")
            if archive.getinfo(member).file_size != entry['bytes']:
                raise ValueError(f"Resource size mismatch: {entry['path']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = None
            try:
                digest = hashlib.sha256()
                with archive.open(member) as source, tempfile.NamedTemporaryFile(
                    dir=destination.parent, suffix='.restore-tmp', delete=False
                ) as output:
                    staging = Path(output.name)
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                if staging.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                    raise ValueError(f"Resource integrity check failed: {entry['path']}")
                os.replace(staging, destination)
                print('Restored', entry['project_path'])
            finally:
                if staging is not None:
                    staging.unlink(missing_ok=True)
    print('All', len(manifest['files']), 'offline reference files are present and verified; no personal notes were imported.')


def preview(port):
    from flask import redirect, request, abort
    from werkzeug.serving import make_server
    sys.path.insert(0, str(ROOT / 'Tests'))
    from test_offline import prepared_app, PROFILE
    app = prepared_app(ROOT / '.test-data/preview')
    app.device_state.save_profile(PROFILE)

    @app.route('/open-preview')
    def open_preview():
        if request.args.get('gate') != 'test-gate': abort(403)
        response = redirect('/pastor-resources/bible-study')
        response.set_cookie('iowo_device_gate', 'test-gate', httponly=True, samesite='Strict')
        return response

    # This explicit test entry is the only gate exception. All normal study,
    # account and write endpoints retain the existing gate and CSRF checks.
    original_gate = app.before_request_funcs[None][0]
    app.before_request_funcs[None][0] = lambda: None if request.endpoint == 'open_preview' else original_gate()
    print(f'Windows/WebKit shared UI preview: http://127.0.0.1:{port}/open-preview?gate=test-gate', flush=True)
    print('Fictional account only. This is not an Apple Simulator or native iOS validation.', flush=True)
    make_server('127.0.0.1', port, app, threaded=True).serve_forever()


def project():
    """Generate a plain Xcode project on either Windows or macOS."""
    import xml.etree.ElementTree as xml
    objects = {}
    def identity(label): return hashlib.sha256(label.encode()).hexdigest()[:24].upper()
    def add(label, **fields):
        key = identity(label); objects[key] = fields
        return key
    def file(label, path, kind, **extra):
        return add(label, isa='PBXFileReference', lastKnownFileType=kind, path=path, sourceTree='<group>', **extra)
    def build(label, reference, **extra): return add(label, isa='PBXBuildFile', fileRef=reference, **extra)
    def phase(label, kind, files, **extra):
        return add(label, isa=kind, buildActionMask=2147483647, files=files, runOnlyForDeploymentPostprocessing=0, **extra)
    names = ['AppDelegate.swift', 'StudyViewController.swift', 'NativeServices.swift', 'ResourceInstaller.swift',
             'SermonPDF.swift', 'PythonHost.m', 'PythonHost.h', 'IOWOBibleStudy-Bridging-Header.h',
             'Assets.xcassets', 'Info.plist', 'LaunchScreen.storyboard', 'PrivacyInfo.xcprivacy']
    kinds = {'.swift': 'sourcecode.swift', '.m': 'sourcecode.c.objc', '.h': 'sourcecode.c.h',
             '.xcassets': 'folder.assetcatalog', '.plist': 'text.plist.xml', '.storyboard': 'file.storyboard', '.xcprivacy': 'text.xml'}
    refs = {name: file('file:'+name, name, kinds[Path(name).suffix]) for name in names}
    resources = file('resource-folder', 'Resources', 'folder')
    python = file('python-framework', 'Vendor/Python.xcframework', 'wrapper.xcframework')
    app_product = add('app-product', isa='PBXFileReference', explicitFileType='wrapper.application', includeInIndex=0, path='IOWOBibleStudy.app', sourceTree='BUILT_PRODUCTS_DIR')
    test_product = add('test-product', isa='PBXFileReference', explicitFileType='wrapper.cfbundle', includeInIndex=0, path='NativeTests.xctest', sourceTree='BUILT_PRODUCTS_DIR')
    test_file = file('test-file', 'NativeTests.swift', 'sourcecode.swift')
    source_group = add('sources-group', isa='PBXGroup', children=list(refs.values()), path='Sources', sourceTree='<group>')
    test_group = add('tests-group', isa='PBXGroup', children=[test_file], path='Tests', sourceTree='<group>')
    products = add('products-group', isa='PBXGroup', children=[app_product, test_product], name='Products', sourceTree='<group>')
    main_group = add('main-group', isa='PBXGroup', children=[source_group, test_group, resources, python, products], sourceTree='<group>')
    sources_phase = phase('sources-phase', 'PBXSourcesBuildPhase', [build('build:'+n, refs[n]) for n in names if n.endswith(('.swift', '.m'))])
    frameworks_phase = phase('frameworks-phase', 'PBXFrameworksBuildPhase', [build('python-link', python)])
    resources_phase = phase('resources-phase', 'PBXResourcesBuildPhase', [build('resources', resources)] + [build('build:'+n, refs[n]) for n in ['Assets.xcassets', 'LaunchScreen.storyboard', 'PrivacyInfo.xcprivacy']])
    script_phase = phase('python-phase', 'PBXShellScriptBuildPhase', [], inputPaths=[], outputPaths=[],
                         name='Embed Python and convert extension modules', shellPath='/bin/bash',
                         shellScript='/bin/bash "$PROJECT_DIR/Scripts/embed-python.sh"\n', alwaysOutOfDate=1)
    embed_phase = phase('embed-phase', 'PBXCopyFilesBuildPhase', [build('python-embed', python,
                        settings={'ATTRIBUTES': ['CodeSignOnCopy', 'RemoveHeadersOnCopy']})], dstPath='', dstSubfolderSpec=10, name='Embed Frameworks')
    common = {'SDKROOT': 'iphoneos', 'IPHONEOS_DEPLOYMENT_TARGET': '16.0', 'SWIFT_VERSION': '5.0',
              'CLANG_ENABLE_MODULES': 'YES', 'CLANG_ENABLE_OBJC_ARC': 'YES', 'TARGETED_DEVICE_FAMILY': '1,2',
              'ENABLE_USER_SCRIPT_SANDBOXING': 'NO', 'ENABLE_BITCODE': 'NO', 'CODE_SIGN_STYLE': 'Automatic',
              'SUPPORTED_PLATFORMS': 'iphoneos iphonesimulator'}
    config_groups = {'project': [], 'app': [], 'test': []}
    for mode in ['Debug', 'Release']:
        settings = {**common, 'SWIFT_OPTIMIZATION_LEVEL': '-Onone' if mode == 'Debug' else '-O',
                    'DEBUG_INFORMATION_FORMAT': 'dwarf' if mode == 'Debug' else 'dwarf-with-dsym',
                    'ONLY_ACTIVE_ARCH': 'YES' if mode == 'Debug' else 'NO'}
        config_groups['project'].append(add('project:'+mode, isa='XCBuildConfiguration', buildSettings=settings, name=mode))
        settings = {'PRODUCT_BUNDLE_IDENTIFIER': 'org.iowo.d4.biblestudy', 'PRODUCT_NAME': 'IOWOBibleStudy',
                    'MARKETING_VERSION': '1.0.4', 'CURRENT_PROJECT_VERSION': '5', 'INFOPLIST_FILE': 'Sources/Info.plist',
                    'SWIFT_OBJC_BRIDGING_HEADER': 'Sources/IOWOBibleStudy-Bridging-Header.h', 'ASSETCATALOG_COMPILER_APPICON_NAME': 'AppIcon',
                    'LD_RUNPATH_SEARCH_PATHS': ['$(inherited)', '@executable_path/Frameworks'],
                    'FRAMEWORK_SEARCH_PATHS': ['$(inherited)', '$(PROJECT_DIR)/Vendor', '$(BUILT_PRODUCTS_DIR)'],
                    'HEADER_SEARCH_PATHS': ['$(inherited)', '$(BUILT_PRODUCTS_DIR)/Python.framework/Headers'],
                    'ENABLE_TESTABILITY': 'YES' if mode == 'Debug' else 'NO'}
        config_groups['app'].append(add('app:'+mode, isa='XCBuildConfiguration', buildSettings=settings, name=mode))
        settings = {'PRODUCT_BUNDLE_IDENTIFIER': 'org.iowo.d4.biblestudy.NativeTests', 'PRODUCT_NAME': 'NativeTests',
                    'GENERATE_INFOPLIST_FILE': 'YES', 'TEST_HOST': '$(BUILT_PRODUCTS_DIR)/IOWOBibleStudy.app/IOWOBibleStudy',
                    'BUNDLE_LOADER': '$(TEST_HOST)', 'SWIFT_VERSION': '5.0'}
        config_groups['test'].append(add('test:'+mode, isa='XCBuildConfiguration', buildSettings=settings, name=mode))
    lists = {name: add(name+'-configs', isa='XCConfigurationList', buildConfigurations=items,
                      defaultConfigurationIsVisible=0, defaultConfigurationName='Release') for name, items in config_groups.items()}
    app_target = add('app-target', isa='PBXNativeTarget', buildConfigurationList=lists['app'],
                     buildPhases=[sources_phase, frameworks_phase, resources_phase, script_phase, embed_phase], buildRules=[], dependencies=[],
                     name='IOWOBibleStudy', productName='IOWOBibleStudy', productReference=app_product, productType='com.apple.product-type.application')
    proxy = add('test-proxy', isa='PBXContainerItemProxy', containerPortal=identity('project'), proxyType=1,
                remoteGlobalIDString=app_target, remoteInfo='IOWOBibleStudy')
    dependency = add('test-dependency', isa='PBXTargetDependency', target=app_target, targetProxy=proxy)
    test_phase = phase('test-sources', 'PBXSourcesBuildPhase', [build('native-test-build', test_file)])
    test_frameworks = phase('test-frameworks', 'PBXFrameworksBuildPhase', [])
    test_target = add('test-target', isa='PBXNativeTarget', buildConfigurationList=lists['test'],
                      buildPhases=[test_phase, test_frameworks], buildRules=[], dependencies=[dependency],
                      name='NativeTests', productName='NativeTests', productReference=test_product, productType='com.apple.product-type.bundle.unit-test')
    project_id = add('project', isa='PBXProject', attributes={'LastUpgradeCheck': '1600', 'TargetAttributes': {
        app_target: {'CreatedOnToolsVersion': '16.0'}, test_target: {'CreatedOnToolsVersion': '16.0', 'TestTargetID': app_target}}},
        buildConfigurationList=lists['project'], compatibilityVersion='Xcode 14.0', developmentRegion='en', hasScannedForEncodings=0,
        knownRegions=['en', 'Base'], mainGroup=main_group, productRefGroup=products, projectDirPath='', projectRoot='', targets=[app_target, test_target])
    def encode(value, level=0):
        if isinstance(value, dict):
            return '{\n'+''.join('  '*(level+1)+json.dumps(str(k))+' = '+encode(v, level+1)+';\n' for k, v in value.items())+'  '*level+'}'
        if isinstance(value, list): return '('+', '.join(encode(v, level+1) for v in value)+')'
        return str(value) if isinstance(value, int) else json.dumps(str(value), ensure_ascii=True)
    folder = ROOT/'IOWOBibleStudy.xcodeproj'
    folder.mkdir(exist_ok=True)
    (folder/'project.pbxproj').write_text('// !$*UTF8*$!\n'+encode({'archiveVersion': 1, 'classes': {}, 'objectVersion': 56,
                                                              'objects': objects, 'rootObject': project_id})+'\n', encoding='utf-8')
    scheme = xml.Element('Scheme', LastUpgradeVersion='1600', version='1.3')
    def reference(parent, target, product, name):
        xml.SubElement(parent, 'BuildableReference', BuildableIdentifier='primary', BlueprintIdentifier=target,
                       BuildableName=product, BlueprintName=name, ReferencedContainer='container:IOWOBibleStudy.xcodeproj')
    action = xml.SubElement(scheme, 'BuildAction', parallelizeBuildables='YES', buildImplicitDependencies='YES')
    entry = xml.SubElement(xml.SubElement(action, 'BuildActionEntries'), 'BuildActionEntry',
                           **{key: 'YES' for key in ['buildForTesting', 'buildForRunning', 'buildForProfiling', 'buildForArchiving', 'buildForAnalyzing']})
    reference(entry, app_target, 'IOWOBibleStudy.app', 'IOWOBibleStudy')
    test = xml.SubElement(scheme, 'TestAction', buildConfiguration='Debug', selectedDebuggerIdentifier='Xcode.DebuggerFoundation.Debugger.LLDB', selectedLauncherIdentifier='Xcode.IDEFoundation.Launcher.LLDB', shouldUseLaunchSchemeArgsEnv='YES')
    reference(xml.SubElement(xml.SubElement(test, 'Testables'), 'TestableReference', skipped='NO'), test_target, 'NativeTests.xctest', 'NativeTests')
    for name, mode in [('LaunchAction', 'Debug'), ('ProfileAction', 'Release')]:
        action = xml.SubElement(scheme, name, buildConfiguration=mode, useCustomWorkingDirectory='NO', debugDocumentVersioning='YES')
        if name == 'LaunchAction': action.set('selectedDebuggerIdentifier', 'Xcode.DebuggerFoundation.Debugger.LLDB'); action.set('selectedLauncherIdentifier', 'Xcode.IDEFoundation.Launcher.LLDB')
        reference(xml.SubElement(action, 'BuildableProductRunnable', runnableDebuggingMode='0'), app_target, 'IOWOBibleStudy.app', 'IOWOBibleStudy')
    xml.SubElement(scheme, 'AnalyzeAction', buildConfiguration='Debug')
    xml.SubElement(scheme, 'ArchiveAction', buildConfiguration='Release', revealArchiveInOrganizer='YES')
    scheme_folder = folder/'xcshareddata/xcschemes'; scheme_folder.mkdir(parents=True, exist_ok=True)
    xml.ElementTree(scheme).write(scheme_folder/'IOWOBibleStudy.xcscheme', encoding='utf-8', xml_declaration=True)
    print('Generated Xcode project and shared scheme, including the native PDF test target.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'preview', 'project', 'restore-library'])
    parser.add_argument('--from-archive', type=Path, help='Local standalone APK or complete iOS source handoff ZIP.')
    parser.add_argument('--port', type=int, default=8766)
    options = parser.parse_args()
    if options.command == 'verify': verify()
    elif options.command == 'project': project()
    elif options.command == 'restore-library':
        if options.from_archive is None:
            parser.error('restore-library requires --from-archive')
        restore_library(options.from_archive)
    else: preview(options.port)
