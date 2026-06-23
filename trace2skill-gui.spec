# -*- mode: python ; coding: utf-8 -*-
import importlib.util
from pathlib import Path

block_cipher = None

_rich_unicode_data = []
_spec = importlib.util.find_spec('rich._unicode_data')
if _spec and _spec.submodule_search_locations:
    for p in Path(_spec.submodule_search_locations[0]).glob('*.py'):
        if p.stem != '__init__':
            _rich_unicode_data.append(f'rich._unicode_data.{p.stem}')

a = Analysis(
    ['gui_entrypoint.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
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
        'schedule',
        'sqlite3',
        'json',
        'pathlib',
        'typing_extensions',
        'http.server',
        'webbrowser',
        'socket',
        'threading',
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
