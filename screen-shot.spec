# -*- mode: python ; coding: utf-8 -*-

import os
import rapidocr as _rapidocr_pkg

# OCR 模型（PP-OCRv6，中英混合）随包分发，需显式打包进 exe
_rapidocr_models = os.path.join(os.path.dirname(_rapidocr_pkg.__file__), 'models')
_ocr_datas = [(os.path.join(_rapidocr_models, f), os.path.join('rapidocr', 'models'))
              for f in os.listdir(_rapidocr_models) if f.endswith('.onnx')]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=_ocr_datas,
    hiddenimports=['pystray._win32', 'pystray._xorg', 'pynput._util.win32', 'pynput.keyboard._win32', 'pynput.mouse._win32',
                   'mss.windows', 'mss.windows.gdi',
                   'rapidocr', 'rapidocr.main', 'rapidocr.utils', 'onnxruntime', 'numpy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='screen-shot',
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
)
