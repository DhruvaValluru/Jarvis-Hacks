# -*- mode: python -*-
# PyInstaller spec for Alfred's app folder. packaging/build.ps1 runs it on Windows from windows-mvp:
#   python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\alfred.spec
#
# dist\Alfred\
#   Alfred.exe       the desktop app (jarvis.app.main), windowed: no console window
#   alfred-cli.exe   console: --engine (what the app starts, see jarvis.service.engine_command),
#                    --selftest, --remove-user-data, or the jarvis command line
#   _internal\       Python, the packages and the data files, shared by both
# build.ps1 then adds node\node.exe and telegram\ (the Telegram bridge), next to the exes where
# jarvis.service.bridge_command() looks for them.
#
# Paths the code relies on inside _internal (config.MVP_DIR is _internal when frozen): PRIVACY.md,
# requirements.lock.txt, jarvis\app\ui\, and each package's own data files next to its modules.

import importlib.util
import re
import sys
from pathlib import Path

from PyInstaller.compat import is_win
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

HERE = Path(SPECPATH)  # noqa: F821 - set by PyInstaller
MVP = HERE.parent
BUILD = MVP / "build"
VERSION = re.search(r'__version__ = "([^"]+)"', (MVP / "jarvis" / "__init__.py").read_text())[1]


def package_dir(name: str) -> Path:
    """Where an installed package lives, without importing it."""
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise SystemExit(f"alfred.spec: {name} isn't installed. Install requirements.lock.txt first.")
    if spec.submodule_search_locations:
        return Path(list(spec.submodule_search_locations)[0])
    return Path(spec.origin).parent


def jarvis_modules() -> list[str]:
    """Every module of the jarvis package. Many are imported inside functions or by name only."""
    names = []
    for path in sorted((MVP / "jarvis").rglob("*.py")):
        parts = path.relative_to(MVP).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if name != "jarvis.app.__main__":  # it runs the app when imported; Alfred.exe has its own entry
            names.append(name)
    return names


def comtypes_gen_modules() -> list[str]:
    """The COM wrappers build.ps1 generated from Windows' UI Automation type library (comtypes.gen.*).
    Bundled, so the app never has to generate them on the user's PC."""
    gen = package_dir("comtypes") / "gen"
    if not (gen / "UIAutomationClient.py").is_file():
        raise SystemExit("alfred.spec: comtypes.gen.UIAutomationClient is missing. Run packaging\\build.ps1, "
                         "which generates it first (comtypes.client.GetModule('UIAutomationCore.dll')).")
    return ["comtypes.gen"] + [f"comtypes.gen.{p.stem}" for p in sorted(gen.glob("*.py")) if p.stem != "__init__"]


def version_file(name: str, description: str) -> str:
    """A Windows version resource (Properties > Details) for one exe."""
    numbers = [int(n) for n in re.findall(r"\d+", VERSION)[:4]]
    numbers = tuple(numbers + [0] * (4 - len(numbers)))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Alfred'),
      StringStruct('FileDescription', {description!r}),
      StringStruct('FileVersion', {VERSION!r}),
      StringStruct('InternalName', {name!r}),
      StringStruct('OriginalFilename', {name + '.exe'!r}),
      StringStruct('ProductName', 'Alfred'),
      StringStruct('ProductVersion', {VERSION!r})])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = BUILD / f"version-{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


if not is_win:
    raise SystemExit("alfred.spec builds the Windows app: run packaging\\build.ps1 on Windows.")

ICON = BUILD / "alfred.ico"
if not ICON.is_file():  # build.ps1 draws it first; this covers running PyInstaller by hand
    sys.path.insert(0, str(HERE))
    import make_icon

    make_icon.write_ico(ICON)

# --- what goes in ---------------------------------------------------------------
hiddenimports = jarvis_modules() + comtypes_gen_modules() + [
    # pywin32 (imported inside functions all over jarvis)
    "pythoncom", "pywintypes", "win32api", "win32con", "win32gui", "win32process", "win32job", "win32cred",
    "win32clipboard", "win32com", "win32com.client",
    # pywin32 imports this from C code to turn a credential's LastWritten time into a datetime (CredRead),
    # so PyInstaller can't see it; without it reading a saved key fails with ModuleNotFoundError.
    "win32timezone",
    # COM, UI Automation, volume
    "comtypes", "comtypes.client", "comtypes.stream", "comtypes.automation", "comtypes.typeinfo",
    "comtypes.persist", "uiautomation", "pycaw", "pycaw.pycaw",
    # Speech recognition and audio (faster_whisper loads huggingface_hub's parts lazily, by name)
    "faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "hf_xet", "sounddevice", "numpy",
    *collect_submodules("huggingface_hub", filter=lambda name: ".cli" not in name and ".commands" not in name),
    # Claude
    "anthropic", "truststore",
    # The window (pywebview on .NET WinForms and Edge WebView2) and the tray icon
    "webview", "webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "pythonnet", "clr_loader",
    "pystray", "pystray._win32", "PIL.Image", "PIL.ImageDraw", "psutil",
]

datas = [
    (str(MVP / "jarvis" / "app" / "ui"), "jarvis/app/ui"),  # the window's HTML, CSS and JavaScript
    (str(MVP / "PRIVACY.md"), "."),  # the data notice (consent.notice_markdown)
    (str(MVP / "requirements.lock.txt"), "."),  # what python -m jarvis --doctor compares against
]
datas += collect_data_files("faster_whisper")  # assets/silero_vad*.onnx
datas += collect_data_files("webview", subdir="lib")  # WebView2 .NET interop DLLs and WebView2Loader.dll
datas += collect_data_files("webview", subdir="js")
datas += collect_data_files("pythonnet")  # runtime/Python.Runtime.dll (.NET, so not a "binary")
for dist in ("anthropic", "faster-whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface-hub",
             "hf-xet", "tqdm", "numpy", "pywebview", "pystray", "pythonnet", "clr-loader", "pydantic", "pydantic-core"):
    datas += copy_metadata(dist)  # some packages check their own or each other's version at run time

binaries = []
for package in ("ctranslate2", "onnxruntime", "av", "webview", "clr_loader"):
    binaries += collect_dynamic_libs(package)  # DLLs loaded by path from the package's own folder
binaries += [
    # uiautomation loads this from its bin folder for bitmaps (screenshots of controls).
    (str(package_dir("uiautomation") / "bin" / "UIAutomationClient_VC140_X64.dll"), "uiautomation/bin"),
    # sounddevice loads PortAudio from _sounddevice_data (the contrib hook collects it too).
    (str(package_dir("_sounddevice_data") / "portaudio-binaries" / "libportaudio64bit.dll"),
     "_sounddevice_data/portaudio-binaries"),
]

EXCLUDES = ["tkinter", "_tkinter", "pytest", "_pytest", "pluggy", "iniconfig", "py",
            "torch", "transformers", "tensorflow", "IPython", "matplotlib"]

a = Analysis(  # noqa: F821 - PyInstaller's spec globals
    [str(HERE / "alfred_app.py"), str(HERE / "alfred_cli.py")],
    pathex=[str(MVP)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821


def scripts_for(entry: str) -> list:
    """One Analysis covers both programs; each exe runs PyInstaller's bootstrap and only its own script."""
    others = {"alfred_app", "alfred_cli"} - {entry}
    return [script for script in a.scripts if script[0] not in others]


COMMON = dict(exclude_binaries=True, debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
              disable_windowed_traceback=False, argv_emulation=False, target_arch=None, codesign_identity=None,
              entitlements_file=None, icon=[str(ICON)])
app_exe = EXE(  # noqa: F821
    pyz, scripts_for("alfred_app"), [], name="Alfred", console=False,
    version=version_file("Alfred", "Alfred"), **COMMON,
)
cli_exe = EXE(  # noqa: F821
    pyz, scripts_for("alfred_cli"), [], name="alfred-cli", console=True,
    version=version_file("alfred-cli", "Alfred command line and engine"), **COMMON,
)
coll = COLLECT(  # noqa: F821
    app_exe, cli_exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Alfred",
)
