# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CAPI backend API server
# Build with: pyinstaller capi_api.spec

import os
from pathlib import Path

block_cipher = None

# Root of the project
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'capi_api_entry.py')],
    pathex=[str(ROOT), str(ROOT / 'src')],
    binaries=[],
    datas=[
        # Include all CSV data files
        (str(ROOT / 'data'), 'data'),
        # Include src python files (needed for subprocess calls like main.py, snapshot_now.py)
        (str(ROOT / 'src'), 'src'),
        # Include snapshot_now.py at root level
        (str(ROOT / 'snapshot_now.py'), '.'),
        # Include outputs folder placeholder (empty)
    ],
    hiddenimports=[
        # FastAPI / uvicorn internals
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.loops.uvloop',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.off',
        'uvicorn.lifespan.on',
        'fastapi',
        'fastapi.middleware.cors',
        'starlette',
        'starlette.middleware',
        'starlette.middleware.cors',
        # Database
        'pyodbc',
        'sqlalchemy',
        'sqlalchemy.dialects.mssql',
        'sqlalchemy.dialects.mssql.pyodbc',
        # Data
        'pandas',
        'numpy',
        'simpy',
        # Other
        'dotenv',
        'python_dotenv',
        'email_validator',
        'h11',
        # Project modules
        'data_gathering',
        'simulation',
        'main',
        'json_export',
        'livedata_export',
        'history_export',
        'snapshot_accumulator',
        'cycle_time_utils',
        'dashboard',
        'realtime_dashboard',
        'initial_state',
        'validate_schema',
        'scheduler',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy ML/science libs not used by CAPI — exclude to keep exe small and fast
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'scipy', 'sklearn', 'skimage',
        'matplotlib', 'PIL', 'Pillow',
        'cv2', 'pygame',
        'IPython', 'ipykernel', 'jupyter',
        'notebook', 'nbconvert',
        'sympy', 'numba',
        'pyarrow', 'h5py',
        'onnxruntime', 'onnx',
        'psutil', 'psycopg2', 'MySQLdb',
        'cx_Oracle', 'pysqlite2',
        'pytest', 'py', 'pygments',
        'win32com', 'win32api',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='capi_api',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,      # keep console window so logs are visible
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
