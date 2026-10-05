# Self-contained Windows release: every native dependency travels with the EXE.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

a = Analysis(
    ['LingRead.py'],
    pathex=['src'],
    binaries=[(str(p), '.') for p in (Path(sys.base_prefix) / 'DLLs').glob('lib*-3-x64.dll')]
             + collect_dynamic_libs('uiautomation'),
    datas=[],
    hiddenimports=['_cffi_backend']
                  + collect_submodules('uiautomation')
                  + collect_submodules('comtypes', filter=lambda n: not n.startswith('comtypes.test')),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'IPython', 'torch', 'tensorflow',
              'openpyxl', 'pytest', 'notebook', 'tkinter'],
    noarchive=False,
    optimize=0,
)
# Use Windows' ICU ABI; never package an incompatible Poppler DLL from PATH.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='LingRead',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    runtime_tmpdir=None,
)
