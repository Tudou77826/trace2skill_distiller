# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the trace2skill-gui single executable.

The exe is dual-purpose: no args opens the PySide6 desktop window; CLI args
delegate to the trace2skill Click group. ``console=True`` so CLI output is
visible (the GUI window is unaffected).
"""

import importlib.util
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# rich._unicode_data submodules (lazy-loaded by rich).
_rich_unicode_data = []
_spec = importlib.util.find_spec('rich._unicode_data')
if _spec and _spec.submodule_search_locations:
    for p in Path(_spec.submodule_search_locations[0]).glob('*.py'):
        if p.stem != '__init__':
            _rich_unicode_data.append(f'rich._unicode_data.{p.stem}')

# PySide6 needs its plugins (platforms, styles, imageformats, ...) collected
# as binaries/datas in addition to the pure-python submodules.
_pyside_datas, _pyside_bins, _pyside_hidden = collect_all('PySide6')
_shiboken_datas, _shiboken_bins, _shiboken_hidden = collect_all('shiboken6')

a = Analysis(
    ['gui_entrypoint.py'],
    pathex=['src'],
    binaries=_pyside_bins + _shiboken_bins,
    datas=_pyside_datas + _shiboken_datas,
    hiddenimports=[
        'click',
        'openai',
        'httpx',
        'httpcore',
        'h11',
        'anyio',
        'sniffio',
        'certifi',
        'idna',
        'h2',
        'socksio',
        'pydantic',
        'pydantic.deprecated.decorator',
        'pydantic_core',
        'annotated_types',
        'yaml',
        'rich',
        'rich._unicode_data',
        *_rich_unicode_data,
        'markdown_it',
        'mdurl',
        'sqlite3',
        'json',
        'pathlib',
        'typing_extensions',
        # PySide6
        *_pyside_hidden,
        *_shiboken_hidden,
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'PySide6.QtCore',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas',
        'scipy', 'PIL', 'pytest', 'IPython',
    ],
    noarchive=False,
    optimize=1,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='trace2skill-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
