# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import sys
from pathlib import Path

block_cipher = None

# Collect rich._unicode_data submodules (lazy-loaded by rich)
_rich_unicode_data = []
_spec = importlib.util.find_spec('rich._unicode_data')
if _spec and _spec.submodule_search_locations:
    for p in Path(_spec.submodule_search_locations[0]).glob('*.py'):
        mod = f'rich._unicode_data.{p.stem}'
        if p.stem != '__init__':
            _rich_unicode_data.append(mod)

a = Analysis(
    ['entrypoint.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        # click
        'click',
        # openai / httpx
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
        # pydantic
        'pydantic',
        'pydantic.deprecated.decorator',
        'pydantic_core',
        'pydantic_settings',
        'annotated_types',
        # pyyaml
        'yaml',
        # rich
        'rich',
        'rich._unicode_data',
        *_rich_unicode_data,
        'markdown_it',
        'mdurl',
        # schedule
        'schedule',
        # standard lib
        'sqlite3',
        'json',
        'pathlib',
        'typing_extensions',
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
    [],
    exclude_binaries=True,
    name='trace2skill',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='trace2skill',
)
