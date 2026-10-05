"""Build one portable EXE and package reviewed source. Python 3.12 / Windows.

Install project requirements first. Run --help for arguments.
The project and its backups are read-only inputs; output is created separately.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import marshal
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

TESTS = ('test_document_binding.py', 'test_diagnostics.py', 'test_foxit_page_number.py',
         'test_gui_reading_start.py', 'test_line_wrap.py', 'test_manual_location.py',
         'test_punctuation_queue.py', 'test_reading_regressions.py',
         'test_standalone_release.py', 'test_voice_switching.py')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def audit(exe, root, project, files):
    """Inspect packed Python modules and dependencies for local path leakage.

    This detects known local paths, not arbitrary personal facts or all secrets.
    Vendor build paths in original third-party libraries are not removed.
    """
    from PyInstaller.archive.readers import CArchiveReader
    paths = (str(Path.home()), str(project))
    needles = set()
    for path in paths:
        for spelling in (path, path.replace('\\','/'), path.replace('\\','\\\\')):
            for enc in ('utf-8','utf-16-le'):
                needles.add(spelling.lower().encode(enc))
    count = 0
    def scan(name, data):
        nonlocal count
        count += 1
        if any(n in data.lower() for n in needles):
            raise ValueError('Local path found in: '+name)
        if data.startswith(b'PK\x03\x04'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for entry in z.infolist():
                    if not entry.is_dir(): scan(name+'!'+entry.filename,z.read(entry))
    for file in files:
        scan(file.relative_to(root).as_posix(),file.read_bytes())
    archive = CArchiveReader(str(exe))
    for name, entry in archive.toc.items():
        if entry[-1] == 'z':
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                code = pyz.extract(module)
                if code is not None: scan(module,marshal.dumps(code))
        else:
            scan(name,archive.extract(name))
    return count

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, default=Path.cwd()/'release')
    parser.add_argument('--skip-build', action='store_true',
                        help='Use existing dist/LingRead.exe; verify its version before packaging')
    args = parser.parse_args()
    project, output = args.project.resolve(), args.output.resolve()
    # Never write inside a backups tree, including through resolved symlinks.
    if any(p.lower() == 'backups' for p in output.parts):
        parser.error('Output must not be inside backups')
    init = (project/'src/lingread/__init__.py').read_text('utf-8')
    match = re.search(r'__version__\s*=\s*[\'"]([0-9]+\.[0-9]+\.[0-9]+)[\'"]',init)
    if not match: parser.error('Cannot read package version')
    version = match.group(1)
    output.mkdir(parents=True,exist_ok=True)
    final = output/f'LingRead-v{version}-source-and-portable.zip'
    checksum = final.with_suffix('.sha256.txt')
    if final.exists() or checksum.exists():
        parser.error('Output already exists; choose another output folder')

    with tempfile.TemporaryDirectory(prefix='lingread-release-') as tmp:
        stage = Path(tmp)/f'LingRead-v{version}'
        stage.mkdir()
        def copy(relative):
            src = (project/relative).resolve()
            if not src.is_relative_to(project) or 'backups' in [p.lower() for p in src.parts]:
                raise ValueError('Input escapes project or enters backups')
            dest = stage/relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src,dest)
        for name in ('LingRead.py','LingReadStandalone.spec','requirements.txt','启动聆阅.bat'):
            copy(name)
        for file in (project/'src').rglob('*.py'):
            if '__pycache__' not in file.parts: copy(file.relative_to(project))
        for name in TESTS:
            if (project/'tests'/name).exists(): copy(Path('tests')/name)
        test = stage/'tests/test_reading_regressions.py'
        if test.exists():
            content = test.read_text('utf-8')
            marker = '    def test_repeated_glyphs_on_scrolled_foxit_page_still_register(self):'
            if marker in content and 'Personal screenshot fixtures' not in content:
                content = content.replace(marker,
                    "    @unittest.skip('Personal screenshot fixtures excluded from public release')\n"+marker)
                test.write_text(content,'utf-8')
        # Generate public instructions rather than copying conversation/history docs.
        (stage/'README.txt').write_text(f'''LingRead v{version} — source and portable executable
Run 启动聆阅.bat or dist/LingRead.exe. No installer is included.
Source: src/; entry point: LingRead.py.
Build: python -m pip install -r requirements.txt
       python -m PyInstaller --noconfirm LingReadStandalone.spec
Tests: python -m unittest discover -s tests -v
Set LINGREAD_RELEASE_EXE to dist/LingRead.exe to enable standalone release tests.
Personal screenshot fixtures are excluded; the dependent test is skipped.

Excluded: backups, logs, caches, preferences, screenshots, PDFs, conversation
records, local debug scripts, installers and directory bundles.
Third-party libraries retain original vendor metadata and build paths.
Online TTS sends spoken text to its service. At runtime the app creates local
audio caches, settings and diagnostics in the current user's .lingread folder.
No existing user reading data or preferences are shipped.
''','utf-8')
        tools = stage/'tools'
        tools.mkdir()
        shutil.copyfile(Path(__file__).resolve(),tools/'package_portable.py')
        exe = stage/'dist/LingRead.exe'
        if args.skip_build:
            copy('dist/LingRead.exe')
        else:
            subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',
                '--workpath',str(Path(tmp)/'build'),'LingReadStandalone.spec'],
                cwd=stage,check=True)
        from PyInstaller.archive.readers import CArchiveReader
        embedded = CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz').extract('lingread')
        if version not in embedded.co_consts:
            raise ValueError('EXE version does not match source version')
        # Build intermediates, if any, never go into the release.
        files = [f for f in stage.rglob('*') if f.is_file() and '__pycache__' not in f.parts]
        if [f.relative_to(stage).as_posix() for f in files if f.suffix.lower()=='.exe'] != ['dist/LingRead.exe']:
            raise ValueError('Expected exactly one standalone executable')
        count = audit(exe,stage,project,files)
        manifest = [dict(path=f.relative_to(stage).as_posix(),size=f.stat().st_size,
                         sha256=digest(f.read_bytes())) for f in sorted(files)]
        metadata=stage/'MANIFEST.json'
        metadata.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
        files.append(metadata)
        tempzip = Path(tmp)/'release.zip'
        with zipfile.ZipFile(tempzip,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for f in sorted(files): z.write(f,stage.name+'/'+f.relative_to(stage).as_posix())
        with zipfile.ZipFile(tempzip) as z:
            for row in manifest:
                if digest(z.read(stage.name+'/'+row['path'])) != row['sha256']:
                    raise ValueError('Archive verification failed')
        ziphash = digest(tempzip.read_bytes())
        shutil.copyfile(tempzip,final)
        checksum.write_text(ziphash+'  '+final.name+'\n','ascii')
        print(f'Created {final.name}; {len(files)} files; one EXE; {count} audit entries checked.')

if __name__=='__main__':
    main()
