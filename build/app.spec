# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包設定：VV 實穿草稿產生器（onedir）
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, "..", "app"))
datas = [
    (os.path.join(root, "ui", "index.html"), "ui"),
    (os.path.join(root, "core", "person_detection.onnx"), "core"),
    (os.path.join(root, "core", "person_anchors.npy"), "core"),
    (os.path.join(root, "core", "樣式.json"), "core"),
    (os.path.join(root, "core", "潤飾.json"), "core"),
    (os.path.join(root, "core", "版型文字模板.json"), "core"),
    (os.path.join(root, "core", "模特資料.csv"), "core"),
    (os.path.join(root, "core", "generator.py"), "core"),
    (os.path.join(root, "core", "engine.py"), "core"),
    (os.path.join(root, "core", "vision.py"), "core"),
    (os.path.join(root, "core", "layouts.py"), "core"),
    (os.path.join(root, "core", "camera_overlay.py"), "core"),
    (os.path.join(root, "core", "torn_overlay.py"), "core"),
    (os.path.join(root, "core", "stamp_overlay.py"), "core"),
    (os.path.join(root, "core", "fonts"), os.path.join("core", "fonts")),
    (os.path.join(root, "core", "版型"), os.path.join("core", "版型")),
    (os.path.join(root, "version.py"), "."),
]
binaries, hiddenimports = [], []
for pkg in ("onnxruntime", "pycapcut", "imageio_ffmpeg", "pymediainfo", "webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        print("collect_all skip", pkg, e)
hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "PIL.Image", "numpy", "generator", "engine", "vision", "layouts", "camera_overlay", "torn_overlay", "stamp_overlay", "version"]

a = Analysis(
    [os.path.join(root, "main.py")],
    pathex=[root, os.path.join(root, "core")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "cv2", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="VVReels",
    icon=os.path.join(SPECPATH, "icon.ico"),
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VVReels")
