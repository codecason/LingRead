"""Run with LINGREAD_RELEASE_EXE set; copy only the EXE into a clean directory.

This catches missing bundled native dependencies, independently of the build
machine's Python installation and the adjacent onedir _internal directory.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('LINGREAD_RELEASE_EXE'), 'Set LINGREAD_RELEASE_EXE to the release EXE')
class StandaloneReleaseTests(unittest.TestCase):
    def test_archive_embeds_cffi_backend(self):
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(os.environ['LINGREAD_RELEASE_EXE'])
        self.assertTrue(any(Path(name).name.startswith('_cffi_backend.') for name in archive.toc),
                        'Native CFFI backend is not embedded in the standalone EXE')

    def test_exe_runs_without_adjacent_internal_directory(self):
        source = Path(os.environ['LINGREAD_RELEASE_EXE']).resolve()
        with tempfile.TemporaryDirectory(prefix='lingread-isolated-') as folder:
            destination = Path(folder) / 'LingRead.exe'
            shutil.copy2(source, destination)
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            env.pop('PYTHONHOME', None)
            env['LINGREAD_DATA_DIR'] = str(Path(folder) / 'appdata')
            env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
            process = subprocess.Popen([str(destination), '--help'], cwd=folder, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                code = process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, check=False)
                self.fail('Isolated release did not exit; possible native dependency error dialog')
            self.assertEqual(code, 0, 'Packaged startup failed')
            self.assertFalse((Path(folder) / '_internal').exists())
            crash = Path(env['LINGREAD_DATA_DIR']) / 'crash.log'
            self.assertFalse(crash.exists(), crash.read_text('utf-8') if crash.exists() else '')


if __name__ == '__main__':
    unittest.main()
