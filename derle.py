"""YT-Arsiv exe derlemesi:  python derle.py

Gereken: Python 3.14 + `pip install pyinstaller yt-dlp`.
Cikti : build/YT-Arsiv/ (calisan klasor) ve build/YT-Arsiv-win64.zip (yayin dosyasi).

Cekirdek (ytarsiv_core_cached.pyc) ve araclar/ exe'nin YANINA konur: veritabani (videos.db), downloads/ ve cookies.txt
de orada olusur; bu yuzden klasor yazilabilir bir yere (Program Files disinda) acilmalidir.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

KOK = Path(__file__).resolve().parent
BUILD = KOK / "build"
CIKTI = BUILD / "YT-Arsiv"
ZIP = BUILD / "YT-Arsiv-win64.zip"

# exe'nin yanina kopyalanacak dosyalar (kaynak, hedef goreli yol)
YANINA = [
    ("ytarsiv_core_cached.pyc", "ytarsiv_core_cached.pyc"),
    ("araclar/fpcalc.exe", "araclar/fpcalc.exe"),
    ("araclar/KAYNAK.txt", "araclar/KAYNAK.txt"),
    ("README.md", "README.md"),
    ("THIRD-PARTY.md", "THIRD-PARTY.md"),
]


def main():
    # python derle.py [--ffmpeg C:\yol\ffmpeg.exe]   -> ffmpeg.exe yayin zip'ine araclar/ icine konur (depoda tutulmaz: 87 MB, GPLv3)
    ffmpeg = None
    if "--ffmpeg" in sys.argv:
        ffmpeg = Path(sys.argv[sys.argv.index("--ffmpeg") + 1])
        if not ffmpeg.is_file():
            sys.exit(f"ffmpeg bulunamadi: {ffmpeg}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "YT-Arsiv.spec", "--noconfirm",
         "--distpath", str(BUILD), "--workpath", str(BUILD / "work")],
        cwd=KOK, check=True)
    for kaynak, hedef in YANINA:
        h = CIKTI / hedef
        h.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(KOK / kaynak, h)
    if ffmpeg:
        shutil.copy2(ffmpeg, CIKTI / "araclar" / "ffmpeg.exe")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(CIKTI.rglob("*")):
            if p.is_file():
                z.write(p, Path("YT-Arsiv") / p.relative_to(CIKTI))
    print(f"\nHazir: {CIKTI}\nZip  : {ZIP} ({ZIP.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
