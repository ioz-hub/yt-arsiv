# -*- mode: python ; coding: utf-8 -*-
# PyInstaller tanimi: iki exe (GUI + komut satiri).  Derlemek icin:  python derle.py
#   varsayilan : TEK DOSYA exe'ler (her biri kendi icine gomulu; acilista gecici klasore acilir)
#   --onedir   : klasor modu (YTARSIV_ONEDIR=1): daha hizli acilir ama yuzlerce dosya cikar
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ONEDIR = os.environ.get("YTARSIV_ONEDIR") == "1"

# yt-dlp modulleri exe'nin icine (PYZ) gomulur; ayrica kaynak .py dosyalari DISARI cikarilmaz (eskiden ~1050 dosya).
yt_hidden = collect_submodules("yt_dlp")
yt_datas = collect_data_files("yt_dlp", include_py_files=False)
IKON = os.path.join("ikon", "yt-arsiv.ico")                       # exe simgesi (Gezgin / gorev cubugu) - ikon\ikon_uret.py uretir
gui_datas = yt_datas + [(IKON, "ikon")]                           # GUI penceresi simgesi: exe icine gomulur (sys._MEIPASS\ikon)

# Derlenmis cekirdek (ytarsiv_core_cached.pyc) icinde bu moduller var; PyInstaller .pyc'nin icini analiz etmedigi icin
# acikca belirtilir.
GIZLI = yt_hidden + [
    "ytarsiv_core", "sqlite3", "json", "shutil", "datetime", "time", "re", "pathlib", "difflib",
    "importlib.machinery", "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.simpledialog", "tkinter.filedialog",
]

gui = Analysis(["YT-Arsiv-GUI.pyw"], pathex=["."], binaries=[], datas=gui_datas,
               hiddenimports=GIZLI, noarchive=False)
cli = Analysis(["yt-arsiv.py"], pathex=["."], binaries=[], datas=yt_datas,
               hiddenimports=GIZLI, noarchive=False)

if ONEDIR:
    gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="YT-Arsiv", console=False, upx=False, icon=IKON)
    cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="yt-arsiv-cli", console=True, upx=False, icon=IKON)
    COLLECT(gui_exe, gui.binaries, gui.datas,
            cli_exe, cli.binaries, cli.datas,
            name="YT-Arsiv", upx=False)
else:
    EXE(PYZ(gui.pure), gui.scripts, gui.binaries, gui.datas, [], name="YT-Arsiv", console=False, upx=False, icon=IKON)
    EXE(PYZ(cli.pure), cli.scripts, cli.binaries, cli.datas, [], name="yt-arsiv-cli", console=True, upx=False, icon=IKON)
