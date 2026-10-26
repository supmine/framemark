# PyInstaller build: `pyinstaller framemark.spec`
# macOS -> dist/Framemark.app · Windows -> dist/Framemark.exe · Linux -> dist/Framemark
import sys

a = Analysis(
    ["app.py"],
    datas=[("assets", "assets")],
    hiddenimports=["pillow_heif"],
    # Qt modules the app never touches; keeps the binary much smaller
    excludes=[
        "tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtPdf",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtSvg",
    ],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Framemark", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="Framemark", upx=False)
    app = BUNDLE(
        coll,
        name="Framemark.app",
        bundle_identifier="com.supmine.framemark",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "1.0.0"},
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Framemark", console=False, upx=False)
