# -*- mode: python ; coding: utf-8 -*-
# PyInstaller tanimi: tek klasorde iki exe (GUI + komut satiri). Derlemek icin:  python derle.py
from PyInstaller.utils.hooks import collect_all

yt_datas, yt_binaries, yt_hidden = collect_all("yt_dlp")

# Derlenmis cekirdek (ytarsiv_core_cached.pyc) icinde bu moduller var; PyInstaller .pyc'nin icini analiz etmedigi icin
# acikca belirtilir.
GIZLI = yt_hidden + [
    "ytarsiv_core", "sqlite3", "json", "shutil", "datetime", "time", "re", "pathlib", "difflib",
    "importlib.machinery", "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.simpledialog",
]

gui = Analysis(["YT-Arsiv-GUI.pyw"], pathex=["."], binaries=yt_binaries, datas=yt_datas,
               hiddenimports=GIZLI, noarchive=False)
cli = Analysis(["yt-arsiv.py"], pathex=["."], binaries=yt_binaries, datas=yt_datas,
               hiddenimports=GIZLI, noarchive=False)

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="YT-Arsiv",
              console=False, upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="yt-arsiv-cli",
              console=True, upx=False)

COLLECT(gui_exe, gui.binaries, gui.datas,
        cli_exe, cli.binaries, cli.datas,
        name="YT-Arsiv", upx=False)
