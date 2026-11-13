"""YT-Arsiv core loader: source recovery shim for the verified cached build."""
import difflib
import importlib.machinery
import re
from datetime import datetime as _datetime
from pathlib import Path
import os as _os_yol
import sys as _sys_yol
# Donmus (PyInstaller exe) surumde veri/arac/cekirdek dosyalari exe'nin yaninda durur; kaynaktan calismada bu dosyanin yaninda.
UYGULAMA_DIZINI = (Path(_sys_yol.executable).resolve().parent if getattr(_sys_yol, "frozen", False)
                   else Path(__file__).resolve().parent)
ARAC_DIZINI = UYGULAMA_DIZINI / "araclar"
if ARAC_DIZINI.is_dir():      # ffmpeg.exe / fpcalc.exe buraya konabilir; yt-dlp ffmpeg'i PATH'ten bulur
    _os_yol.environ["PATH"] = str(ARAC_DIZINI) + _os_yol.pathsep + _os_yol.environ.get("PATH", "")
_cached_path = UYGULAMA_DIZINI / "ytarsiv_core_cached.pyc"
_loader = importlib.machinery.SourcelessFileLoader("_ytarsiv_core_cached", str(_cached_path))
_cached = _loader.load_module()
globals().update({k: v for k, v in _cached.__dict__.items() if k not in {"__name__", "__loader__", "__package__"}})
# Eski "001 - ID - baslik" adlarini da tanit.
# Fallback olarak sona ekliyoruz: normal "[videoID]" deseni once denensin, aksi
# halde 11-karakterli artist adlari (Blasterjaxx, Kadebostany gibi) yanlislikla
# video_id olarak yakalaniyor. Ek guvenlik: 11-karakterin icinde en az bir rakam,
# alt-cizgi veya tire olmasi gerek (gercek YouTube ID'leri icin tipik, saf harf
# ad/soyad icin pek rastlanmaz).
VIDEOID_PATTERNS.append(re.compile(
    r"^\d{3}\s*-\s*(?=[A-Za-z0-9]*[\d_-][A-Za-z0-9_-]*\s*-)([A-Za-z0-9_-]{11})\s*-"
))
# Bilerek tekrarlanan videolar: "Ayni Videolar" sonuclarindan cikarilir, dosyalara dokunulmaz.
DUPLICATE_EXCEPTIONS = {"O0XVJxaeHiQ"}  # her Eurodance listesinin basindaki Dj Kip parcasi

_find_duplicates_orig = find_duplicates


def _adlari_tekille(result, conn=None):
    """DISK tekrarlarinda ayni FIZIKSEL dosyanin (sabit bag) birden cok adi tek girdiye iner: bunlar tekrar degildir
    (ek yer tutmaz, icerik ayni) - birini silmek yer acmaz.  disk_dupes'ta fiziksel dosya sayisi < 2 ise girdi duser;
    disk_orphans'ta (DB'de kaydi olmayan adlar) girdi kalir, yalniz adlar tekillesir; ANCAK conn verilmisse ve girdideki
    tum fiziksel dosyalar bir DB kaydinin dosyasiyla AYNIYSA (yalniz fazladan bag adi) girdi de duser.  Arsiv klasorundeki
    (_ListeDisi / _YayındanKalkan / _silinenler) ad yalniz baska ad yoksa temsil eder."""
    import os
    arsiv = (LISTE_DISI_DIR, YAYINDAN_KALKAN_DIR, ESKI_LISTE_DISI_DIR)

    def arsivde(rel):
        return any(parca in arsiv for parca in re.split(r"[\\/]", rel))

    db_kimlikleri = None
    for kategori, en_az in (("disk_dupes", 2), ("disk_orphans", 1)):
        girdiler = result.get(kategori) or {}
        if kategori == "disk_orphans" and girdiler and conn is not None:
            db_kimlikleri = set()
            for (fp,) in conn.execute("SELECT file_path FROM videos WHERE file_path IS NOT NULL"):
                dosya = resolve_video_path(fp)
                try:
                    st = os.stat(dosya) if dosya else None
                except OSError:
                    st = None
                if st:
                    db_kimlikleri.add((st.st_dev, st.st_ino))
        for vid in list(girdiler):
            bilgi = girdiler[vid]
            yollar = bilgi.get("paths", [])
            goreli = bilgi.get("relative_paths") or list(yollar)
            secilen = {}                               # fiziksel kimlik -> (sira, yol, goreli)
            for sira, (yol, rel) in enumerate(zip(yollar, goreli)):
                try:
                    st = os.stat(yol)
                    kimlik = (st.st_dev, st.st_ino)
                except OSError:
                    kimlik = ("yok", sira)             # okunamayan ad kendi basina sayilir
                eski = secilen.get(kimlik)
                if eski is None or (arsivde(eski[2]) and not arsivde(rel)):
                    secilen[kimlik] = (sira if eski is None else eski[0], yol, rel)
            if len(secilen) < en_az or (db_kimlikleri is not None and kategori == "disk_orphans"
                                        and all(k in db_kimlikleri for k in secilen)):
                del girdiler[vid]
                continue
            if len(secilen) < len(yollar):
                sirali = sorted(secilen.values())
                girdiler[vid] = {**bilgi, "paths": [y for _, y, _ in sirali],
                                 **({"relative_paths": [r for _, _, r in sirali]} if "relative_paths" in bilgi else {})}


def _find_duplicates_baslik(conn, include_inactive=False, surum_adaylari=False):
    """Baslik/sure/video_id'ye gore kurulan HAM gruplar.  GUI/CLI bunu degil find_duplicates'i (dosya sonu) cagirir."""
    result = _find_duplicates_orig(conn, include_inactive=include_inactive)
    for cat in ("exact", "disk_dupes", "disk_orphans"):
        for vid in DUPLICATE_EXCEPTIONS:
            result.get(cat, {}).pop(vid, None)
    fuzzy = result.get("fuzzy", {})
    for key in list(fuzzy):
        kept = [e for e in fuzzy[key] if e.get("video_id") not in DUPLICATE_EXCEPTIONS]
        if len(kept) >= 2:
            fuzzy[key] = kept
        else:
            del fuzzy[key]
    _es_gruplari_ekle(conn, result, include_inactive, surum_adaylari=surum_adaylari)
    _adlari_tekille(result, conn)
    _dosyasiz_silinenleri_ayikla(result)
    _tasinanlari_ayikla(conn, result)
    _ayni_dosya_satirlarini_tekille(result)
    return result


def _ayni_dosya_satirlarini_tekille(result):
    """exact/fuzzy gruplarda AYNI fiziksel dosyaya (sabit bag, ayni inode) giden birden cok DB satiri tek sayilir: ek yer tutmaz,
    birini silmek yer acmaz, tekrar degildir.  Temsilci olarak arsiv/_Bilinmeyen disindaki ilk satir secilir.  <2 fiziksel
    dosya kalan grup duser."""
    def oncelik(it):
        fp = it.get("file_path") or ""
        ilk = re.split(r"[\\/]", fp)[0] if fp else ""
        return 1 if ilk in (BILINMEYEN_DIR, LISTE_DISI_DIR, YAYINDAN_KALKAN_DIR, ESKI_LISTE_DISI_DIR) else 0

    for kat in ("exact", "fuzzy"):
        grup = result.get(kat) or {}
        for anahtar in list(grup):
            gorulen, kalan = {}, []
            for it in sorted(grup[anahtar], key=oncelik):
                p = resolve_video_path(it.get("file_path")) if it.get("file_path") else None
                try:
                    st = os.stat(p) if p else None
                except OSError:
                    st = None
                kimlik = (st.st_dev, st.st_ino) if st else None
                if kimlik is not None and kimlik in gorulen:
                    continue
                if kimlik is not None:
                    gorulen[kimlik] = True
                kalan.append(it)
            if len(kalan) >= 2:
                grup[anahtar] = kalan
            else:
                del grup[anahtar]


def _tasinan_kayitlar(conn):
    """Kullanici videoyu bir listeden BASKA listeye tasidiysa eski listedeki satir 'removed_from_playlist' olur ama video
    silinmedi, yalniz tasindi.  Boyle satir = baska bir listede 'active' kaydi var VE dosyasi o kaydin dosyasiyla ayni
    fiziksel dosya (sabit bag) ya da yok.  (video_id, playlist_id) kumesi doner.  Salt okur."""
    canli = {}
    for r in conn.execute("SELECT video_id, file_path FROM videos WHERE status='active'"):
        canli.setdefault(r["video_id"], []).append(r["file_path"])
    sonuc = set()
    for r in conn.execute("SELECT video_id, playlist_id, file_path FROM videos WHERE status='removed_from_playlist'"):
        kardesler = canli.get(r["video_id"])
        if not kardesler:
            continue
        p = resolve_video_path(r["file_path"]) if r["file_path"] else None
        if not (p and p.exists()):
            sonuc.add((r["video_id"], r["playlist_id"]))
            continue
        for fp in kardesler:
            q = resolve_video_path(fp) if fp else None
            try:
                if q and q.exists() and os.path.samefile(p, q):
                    sonuc.add((r["video_id"], r["playlist_id"]))
                    break
            except OSError:
                pass
    return sonuc


def _tasinanlari_ayikla(conn, result):
    """Tasinan (baska listede de duran, ayni dosyayi paylasan) 'liste disi' satirlari Ayni Videolar gruplarindan cikarir;
    grupta <2 uye kalirsa grup da duser."""
    tasinan = _tasinan_kayitlar(conn)
    if not tasinan:
        return
    for kat in ("exact", "fuzzy"):
        grup = result.get(kat) or {}
        for anahtar in list(grup):
            kalan = [it for it in grup[anahtar]
                     if not (it.get("status") == "removed_from_playlist"
                             and (it.get("video_id"), it.get("playlist_id")) in tasinan)]
            if len(kalan) >= 2:
                grup[anahtar] = kalan
            else:
                del grup[anahtar]


def _dosyasiz_silinenleri_ayikla(result):
    """Kullanicinin (Ayni Videolar'dan) elle sildigi kopyalarin DB kaydi ('user_deleted', dosyasi yok) tekrar sayilamaz:
    ortada silinecek/birlestirilecek bir dosya yok.  exact/fuzzy gruplardan cikarilir; grupta <2 uye kalirsa grup da duser."""
    def dosyali_mi(it):
        fp = it.get("file_path")
        p = resolve_video_path(fp) if fp else None
        return bool(p and p.exists())

    for kat in ("exact", "fuzzy"):
        grup = result.get(kat) or {}
        for anahtar in list(grup):
            kalan = [it for it in grup[anahtar] if not (it.get("status") == "user_deleted" and not dosyali_mi(it))]
            if len(kalan) >= 2:
                grup[anahtar] = kalan
            else:
                del grup[anahtar]

# 20 dakikadan uzun videolar (DJ setleri) 1080p ile sinirlanir; kisa klipler en iyi kalitede iner.
LONG_VIDEO_SECONDS = 20 * 60
LONG_VIDEO_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"

# YouTube "bot degilim" kontrolu icin: bu dosya varsa yt-dlp cerezi buradan okur (yoksa hicbir sey degismez).
COOKIES_FILE = UYGULAMA_DIZINI / "cookies.txt"

try:
    import yt_dlp as _yt_dlp

    class _CappedYoutubeDL(_yt_dlp.YoutubeDL):
        _with_cookies = False
        FORMAT_ZORLA = None     # yarim indirmeyi surdururken mevcut parcalarin format kimliklerini zorlar

        def extract_info(self, url, *args, **kwargs):
            # Cerez yalniz YouTube bot kontrolu isterse devreye girer; bozuk cerez normal calismayi engellemez.
            try:
                return super().extract_info(url, *args, **kwargs)
            except _yt_dlp.utils.DownloadError as e:
                if self._with_cookies or not COOKIES_FILE.is_file() or "confirm you" not in str(e):
                    raise
                retry = _CappedYoutubeDL({**self.params, "cookiefile": str(COOKIES_FILE)})
                retry._with_cookies = True
                with retry:
                    return retry.extract_info(url, *args, **kwargs)

        def process_video_result(self, info_dict, download=True):
            fmt = _CappedYoutubeDL.FORMAT_ZORLA or (
                LONG_VIDEO_FORMAT if (info_dict.get("duration") or 0) > LONG_VIDEO_SECONDS else None)
            if download and fmt:
                saved = self.format_selector
                self.format_selector = self.build_format_selector(fmt)
                try:
                    return super().process_video_result(info_dict, download)
                finally:
                    self.format_selector = saved
            return super().process_video_result(info_dict, download)

    _cached.YoutubeDL = YoutubeDL = _CappedYoutubeDL
except ImportError:
    pass

# Ardisik indirmeler arasinda rastgele bekleme (YouTube bot kontrolunu azaltmak icin).
DOWNLOAD_PAUSE_SECONDS = (30, 60)

import random as _random
import time as _time

_download_video_orig = _cached.download_video
_last_download_end = None


def download_video(video_url, dest_dir, playlist_index=None):
    global _last_download_end
    if _last_download_end is not None:
        wait = _random.uniform(*DOWNLOAD_PAUSE_SECONDS) - (_time.monotonic() - _last_download_end)
        if wait > 0:
            _time.sleep(wait)
    try:
        return _download_video_orig(video_url, dest_dir, playlist_index)
    finally:
        _last_download_end = _time.monotonic()


_cached.download_video = download_video


# ---------------------------------------------------------------------------
# KAPI 1: KUCUK DOSYA -> _inceleme/ TASIMA KAPALI.
# Cekirdek (derlenmis) sync_playlists icinde "medyanin %40'i altindaki dosyayi _inceleme/'ye tasi"
# adimi var.  Hedef klasoru _inceleme olan tasima cagrisi burada etkisiz kilinir (dosya yerinde
# kalir, DB/olay/sayac degismez).  Geri acmak icin INCELEME_AKTIF = True.
# ---------------------------------------------------------------------------
INCELEME_AKTIF = False
_move_files_with_sidecars_orig = _cached._move_files_with_sidecars


def _move_files_with_sidecars(src_video, target_dir):
    if not INCELEME_AKTIF and Path(target_dir).name == _cached.INCELEME_SUBDIR:
        return src_video
    return _move_files_with_sidecars_orig(src_video, target_dir)


_cached._move_files_with_sidecars = _move_files_with_sidecars


# ---------------------------------------------------------------------------
# KAPI 2: YT-DLP PARCA DOSYALARI VIDEO DEGILDIR.
# "x [ID].f251.webm" (ses/goruntu parcasi) ve "x [ID].temp.mp4" (yarim birlestirme) bir videonun
# KOPYASI degil, yarim indirme kalintisidir.  Diskte video arayan tum cekirdek islevler
# (dizin, yeniden baglama, kopya bulma) video kimligini extract_video_id'den alir; parca
# adlarinda None donerek hepsi tek yerden dosyayi gormezden gelir.
# ---------------------------------------------------------------------------
_PARCA_RE = re.compile(r"\.(?:f\d{2,4}|temp)(?:\.[A-Za-z0-9]{2,5})?$")
_extract_video_id_orig = _cached.extract_video_id


def extract_video_id(stem):
    if stem and _PARCA_RE.search(str(stem)):
        return None
    return _extract_video_id_orig(stem)


_cached.extract_video_id = extract_video_id


# ---------------------------------------------------------------------------
# KAPI 3: TEKIL KOPYA.  Bir playlist'in bir videoyu "almamasi" tek yerde tutulur: playlist_haric.
# Neden gerekli: ayni video iki playlistte ise tarama, silinen kopyayi diger playlistten yeniden
# baglar/kopyalar ('user_deleted' bunu ENGELLEMEZ - status_reset_file_present).
#   - kopya_kaldir(): kaydi (dosya varsa yan dosyalariyla diskten de) siler ve playlist_haric'e yazar.
#   - fetch_playlist_meta kapisi: haric videolari playlist listesinden cikarir -> tarama gormez.
# GUI/CLI kopya silmeyi YALNIZ kopya_kaldir ile yapar (bkz. test_kapi.py).
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3


def _haric_tablo(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS playlist_haric ("
                 "video_id TEXT NOT NULL, playlist_id INTEGER NOT NULL, eklendi TEXT NOT NULL, neden TEXT, "
                 "PRIMARY KEY (video_id, playlist_id))")


def haric_kaldir(conn, video_id, playlist_id):
    """Haric listesinden cikarir: sonraki taramada video bu playlist icin yeniden eklenir."""
    _haric_tablo(conn)
    conn.execute("DELETE FROM playlist_haric WHERE video_id=? AND playlist_id=?", (video_id, playlist_id))
    conn.commit()


def kopya_kaldir(conn, video_id, playlist_id):
    """Bir playlist'teki video kaydini kaldirir.  Doner: (basari, mesaj).
    - Dosya varsa yan dosyalariyla silinir; YOKSA hata vermez, yalniz kayit silinir.
    - Dosya yolu baska bir kaydin da dosyasiysa (ana kopya) dosyaya DOKUNULMAZ.
    - Video playlist'ten zaten kalkmissa (removed_from_playlist) haric listesine yazilmaz;
      hala playlist'teyse yazilir ki sonraki tarama geri getirmesin."""
    row = conn.execute("SELECT status, file_path FROM videos WHERE video_id=? AND playlist_id=?",
                       (video_id, playlist_id)).fetchone()
    if row is None:
        return False, "kayit yok"
    status, fp = row[0], row[1]
    haric = status != "removed_from_playlist"
    paylasan = bool(fp) and conn.execute(
        "SELECT 1 FROM videos WHERE file_path=? AND NOT (video_id=? AND playlist_id=?) LIMIT 1",
        (fp, video_id, playlist_id)).fetchone() is not None
    if haric:
        _haric_tablo(conn)
        conn.execute("INSERT OR REPLACE INTO playlist_haric (video_id, playlist_id, eklendi, neden) VALUES (?,?,?,?)",
                     (video_id, playlist_id, now_iso(), "kopya_kaldir"))
    kardesler = [] if paylasan else _bag_kardesleri(conn, video_id, playlist_id, fp)
    ok, msg = permanently_delete_video(conn, video_id, playlist_id, delete_file=not paylasan)
    if not ok:
        if haric:
            conn.execute("DELETE FROM playlist_haric WHERE video_id=? AND playlist_id=?", (video_id, playlist_id))
        conn.commit()
        return False, msg
    conn.commit()
    silinen_kardes = _kardesleri_sil(kardesler)
    return True, (msg + (" (dosya baska kayitla paylasildi, silinmedi)" if paylasan else "")
                  + (f" + {silinen_kardes} dosya (ayni dosyanin diger adlari)" if silinen_kardes else ""))


def _bag_kardesleri(conn, video_id, playlist_id, fp):
    """Silinecek dosyanin AYNI FIZIKSEL dosyaya bagli diger adlari (sabit bag) - yalniz hicbir DB kaydi tarafindan
    kullanilmayanlar.  Bunlar silinmezse 'DB disi' artik olarak kalir (Ayni Videolar'da tek basina gorunur)."""
    import os
    hedef = resolve_video_path(fp) if fp else None
    try:
        st = os.stat(hedef) if hedef else None
    except OSError:
        st = None
    if not st or st.st_nlink < 2:
        return []
    kimlik = (st.st_dev, st.st_ino)
    kullanilan = set()
    for (yol,) in conn.execute("SELECT file_path FROM videos WHERE file_path IS NOT NULL "
                               "AND NOT (video_id=? AND playlist_id=?)", (video_id, playlist_id)):
        p = resolve_video_path(yol)
        if p:
            kullanilan.add(os.path.normcase(str(p)))
    bulunan = []
    for kok, _dizinler, dosyalar in os.walk(DOWNLOADS):
        for ad in dosyalar:
            if f"[{video_id}]" not in ad:
                continue
            yol = os.path.join(kok, ad)
            if os.path.normcase(yol) == os.path.normcase(str(hedef)) or os.path.normcase(yol) in kullanilan:
                continue
            try:
                s = os.stat(yol)
            except OSError:
                continue
            if (s.st_dev, s.st_ino) == kimlik:
                bulunan.append(yol)
    return bulunan


def _kardesleri_sil(yollar):
    """_bag_kardesleri sonucunu (ve her adin yan dosyalarini: ayni govde adli .info.json/.webp/...) siler."""
    import os
    n = 0
    for yol in yollar:
        govde, _uzanti = os.path.splitext(yol)
        klasor, ad = os.path.split(govde)
        for aday in [yol] + [os.path.join(klasor, f) for f in os.listdir(klasor)
                             if f != os.path.basename(yol) and f.startswith(ad + ".")]:
            try:
                os.unlink(aday)
                n += 1
            except OSError:
                pass
    return n


# --- BASLIKSIZ KAYITLAR: oynatma listesinde tum alanlari bos gelen (ozel/kaldirilmis) videolar ---
# Cekirdek yalniz "[Private video]" gibi isaretli basliklari yayindan kalkmis sayar; tamamen bos kayit
# "saglam video" sanilip HER taramada indirilmeye calisilir ve hata sayilir.  Bos kayit yt-dlp ile tek tek
# dogrulanir: kalkmissa cekirdegin tanidigi isaretle teslim edilir; canliysa basligi doldurulur;
# dogrulanamazsa (ag, yas siniri, bot kontrolu) DOKUNULMAZ.  Cok sayida bos kayit = bozuk cekim -> dogrulanmaz.
_BASLIKSIZ_ESIK = 5
_KALKMIS_RE = re.compile(r"private video|video is private|video unavailable|no longer available|"
                         r"has been removed|been terminated|does not exist|video has been deleted", re.I)


def _video_durumu(video_id):
    """Doner: ("ok", bilgi) | ("kalkmis", mesaj) | ("belirsiz", mesaj)."""
    yt = globals().get("_yt_dlp")
    if yt is None:
        return "belirsiz", "yt-dlp yok"
    ydl_sinifi = globals().get("_CappedYoutubeDL") or yt.YoutubeDL
    try:
        with ydl_sinifi({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
            bilgi = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return ("ok", bilgi) if bilgi else ("belirsiz", "bos yanit")
    except Exception as e:                                     # noqa: BLE001 - yt-dlp cesitli hata siniflari atar
        mesaj = str(e)
        return ("kalkmis", mesaj) if _KALKMIS_RE.search(mesaj) else ("belirsiz", mesaj)


def _basliksizlari_dogrula(meta, log=None):
    if not isinstance(meta, dict) or meta.get("_is_partial"):
        return
    bos = [e for e in (meta.get("entries") or []) if e and e.get("id") and not e.get("title")]
    if not bos or len(bos) > _BASLIKSIZ_ESIK:
        return
    for e in bos:
        durum, ver = _video_durumu(e["id"])
        if durum == "kalkmis":
            e["title"] = "[Private video]" if "rivate" in str(ver) else "[Video unavailable]"
        elif durum == "ok":
            e["title"] = ver.get("title") or e.get("title")
            for anahtar in ("uploader", "duration"):
                if not e.get(anahtar) and ver.get(anahtar):
                    e[anahtar] = ver[anahtar]
        if log:
            log(f"  [i] basliksiz kayit dogrulandi: {e['id']} -> {durum}")


_fetch_playlist_meta_orig = _cached.fetch_playlist_meta


def fetch_playlist_meta(url, *a, **k):
    meta = _fetch_playlist_meta_orig(url, *a, **k)
    try:
        con = _sqlite3.connect(str(_cached.DB_PATH), timeout=5)
        try:
            rows = con.execute("SELECT h.video_id FROM playlist_haric h JOIN playlists p ON p.id=h.playlist_id "
                               "WHERE p.url=?", (url,)).fetchall()
        finally:
            con.close()
    except _sqlite3.Error:
        rows = []
    if rows and isinstance(meta, dict) and meta.get("entries"):
        haric = {r[0] for r in rows}
        meta["entries"] = [e for e in meta["entries"] if (e or {}).get("id") not in haric]
    _basliksizlari_dogrula(meta, k.get("log"))
    if isinstance(meta, dict) and meta.get("entries") is not None:
        _SON_CANLI[url] = {"ids": {(e or {}).get("id") for e in meta["entries"] if e},
                           "kismi": bool(meta.get("_is_partial"))}
    return meta


# ---------------------------------------------------------------------------
# KANAL EKLEME: playlist yaninda YouTube KANALI da eklenebilir (kanalin tum yuklemeleri bir "playlist" gibi taranir/indirilir).
# yt-dlp'ye ciplak kanal adresi verilirse "Videos/Live/Shorts" sekmelerini ayri oynatma listesi olarak dondurur (cekirdek bunu
# anlamaz); sekme adresi ('/videos') ise dogrudan video listesi dondurur.  Bu yuzden kanal adresleri TEK yerde, ekleme
# aninda '/videos' (ya da '/shorts', '/streams') sekmesine cevrilir; sonrasi playlist ile birebir ayni yoldan gider.
# ---------------------------------------------------------------------------
_KANAL_RE = re.compile(
    r"^(https?://(?:www\.|m\.)?youtube\.com)/(@[^/?#\s]+|(?:channel|c|user)/[^/?#\s]+)(/[^?#\s]*)?(?:[?#].*)?$", re.I)
_KANAL_SEKMELERI = ("/shorts", "/streams")


def kanal_mi(url):
    """URL bir YouTube kanal adresi mi (@ad, /channel/UC..., /c/ad, /user/ad)?  Oynatma listesi adresi ('list=') kanal degildir."""
    u = (url or "").strip()
    return bool(_KANAL_RE.match(u)) and "list=" not in u


def kanal_url_duzenle(url):
    """Kanal adresini video listesi sekmesine cevirir ('.../@ad' -> '.../@ad/videos'); kanal degilse adresi aynen (bosluksuz) dondurur."""
    u = (url or "").strip()
    m = _KANAL_RE.match(u)
    if not m or "list=" in u:
        return u
    kok, kimlik, yol = m.group(1), m.group(2), (m.group(3) or "").rstrip("/").lower()
    return f"{kok}/{kimlik}{yol if yol in _KANAL_SEKMELERI else '/videos'}"


_add_playlist_orig = _cached.add_playlist


def add_playlist(conn, url, alias=""):
    """TEK ekleme kapisi (GUI + CLI): kanal adresi video sekmesine cevrilip kaydedilir, oynatma listesi aynen eklenir."""
    return _add_playlist_orig(conn, kanal_url_duzenle(url), alias)


_cached.add_playlist = add_playlist


_SON_CANLI = {}        # playlist url -> {"ids": canli video idleri, "kismi": yt-dlp kismi mi}  (bu surecteki SON okuma)

_cached.fetch_playlist_meta = fetch_playlist_meta


def guvenli_azalmayi_isle(conn, log=print, canli=None):
    """KAPI: cekirdegin 'sayim dustu' korumasi (>=10 kayitli listede sayim %70'in altina inerse HICBIR cikarma islenmez) kullanici
    BILEREK cok kopyayi listeden cikarinca (ornek Euro3 83->18) cikarmalari hic isletmiyordu; kopyalar 'Ayni Videolar'da kaliyordu.
    Burada YALNIZ su durum islenir: canli liste TAM (kismi degil, bos degil), kayit DB'de 'active' ama canli listede yok VE ayni video
    baska listede 'active' + dosyasi diskte VAR.  Yani hicbir sey kaybolmaz (baska listedeki kopya durur); kayit cekirdekle ayni
    sekilde 'removed_from_playlist' olur (dosyasini 2b arsive_toparla tasir).  Baska listede kopyasi olmayan kayitlara DOKUNULMAZ
    (onlari cekirdegin korumasi karar verir).  canli=None -> bu surecte fetch_playlist_meta'nin son okumalari.  Doner: islenen sayisi.
    Baska listede kopyasi OLMAYAN eksik kayitlar KENDILIGINDEN ne silinmis sayilir ne sessizce yok sayilir: 'silinme_bekleyen'
    tablosuna yazilir ve kullaniciya SORULUR (silinmeyi_onayla / silinmeyi_koru)."""
    canli = _SON_CANLI if canli is None else canli
    _silinme_tablosu(conn)
    toplam = 0
    for pid, url in [(r["id"], r["url"]) for r in conn.execute("SELECT id, url FROM playlists").fetchall()]:
        c = canli.get(url)
        if not c or c["kismi"] or not c["ids"]:
            continue
        # eski bekleyen kayitlar: video listeye geri dondu ya da artik 'active' degil -> sorulacak bir sey kalmadi
        for b in conn.execute("SELECT video_id FROM silinme_bekleyen WHERE playlist_id=?", (pid,)).fetchall():
            durum = conn.execute("SELECT status FROM videos WHERE video_id=? AND playlist_id=?", (b["video_id"], pid)).fetchone()
            if b["video_id"] in c["ids"] or not durum or durum["status"] != "active":
                conn.execute("DELETE FROM silinme_bekleyen WHERE playlist_id=? AND video_id=?", (pid, b["video_id"]))
        for r in conn.execute("SELECT video_id, title, file_path FROM videos WHERE playlist_id=? AND status='active'", (pid,)).fetchall():
            vid = r["video_id"]
            if vid in c["ids"]:
                continue
            kardesler = conn.execute("SELECT file_path FROM videos WHERE video_id=? AND playlist_id<>? AND status='active'",
                                     (vid, pid)).fetchall()
            kardes_yollar = [q for q in ((resolve_video_path(k["file_path"]) if k["file_path"] else None) for k in kardesler)
                             if q and q.exists()]
            if not kardes_yollar:
                conn.execute("INSERT OR IGNORE INTO silinme_bekleyen (playlist_id, video_id, baslik, tespit, durum) "
                             "VALUES (?,?,?,?, 'bekliyor')", (pid, vid, r["title"], now_iso()))
                continue
            conn.execute("UPDATE videos SET status='removed_from_playlist' WHERE video_id=? AND playlist_id=?", (vid, pid))
            log_event(conn, "removed_from_playlist", vid, pid, f"{r['title']} (kopyasi baska listede)")
            toplam += 1
    conn.commit()
    sabit = kopya_satirlarini_sabitle(conn, log=log)
    if toplam:
        log(f"  [-] {toplam} kopya listeden cikarilmis (baska listede kopyasi durur): islendi")
    bekleyen = conn.execute("SELECT COUNT(*) FROM silinme_bekleyen WHERE durum='bekliyor'").fetchone()[0]
    if bekleyen:
        log(f"  [?] {bekleyen} video listede gorunmuyor ama silinmis sayilmadi: ONAYINIZ BEKLENIYOR (GUI: Silinme onaylari / CLI: silinme)")
    return toplam


def kopya_satirlarini_sabitle(conn, log=print):
    """'removed_from_playlist' satiri, AYNI videonun baska listede 'active' kaydi varken kendi (arsivdeki) dosyasina sahip degilse
    (yolu bos / yok ya da kardesin yoluyla AYNI) cekirdek her taramada onu dosyayi yeniden baglayip 'active' yapar, biz tekrar
    cikaririz: sonsuz dongu ('her acilista playlist degisikligi yapildi' mesaji).  Dongu, satira KENDI adini vererek biter:
    kardesin dosyasina sabit bag olarak downloads/_ListeDisi/ altina eklenir (disk yeri harcamaz) ve satirin file_path'i
    oraya alinir; cekirdek dosyasi arsivde olan 'removed' satiri oldugu gibi birakir.  Doner: sabitlenen satir sayisi."""
    n = 0
    for r in conn.execute("SELECT video_id, playlist_id, file_path FROM videos WHERE status='removed_from_playlist'").fetchall():
        vid, pid = r["video_id"], r["playlist_id"]
        kardesler = [q for q in (resolve_video_path(k["file_path"]) if k["file_path"] else None
                                 for k in conn.execute("SELECT file_path FROM videos WHERE video_id=? AND playlist_id<>? "
                                                       "AND status='active'", (vid, pid)).fetchall())
                     if q and q.exists()]
        if not kardesler:
            continue
        kendi = resolve_video_path(r["file_path"]) if r["file_path"] else None
        if kendi and kendi.exists() and os.path.normcase(str(kendi)) not in {os.path.normcase(str(q)) for q in kardesler}:
            continue                          # kendi (kardesten ayri) adi var: arsivdeyse kararli, degilse 2b arsive_toparla tasir
        kaynak = kardesler[0]
        kok = Path(arsiv_kok("removed_from_playlist"))
        hedef = kok / kaynak.name
        try:
            kok.mkdir(parents=True, exist_ok=True)
            if hedef.exists():
                if not os.path.samefile(hedef, kaynak):
                    continue                  # ayni adli FARKLI dosya: dokunma
            else:
                os.link(kaynak, hedef)
        except OSError:
            continue                          # sabit bag kurulamadi (baska surucu vb.): dokunma
        conn.execute("UPDATE videos SET file_path=? WHERE video_id=? AND playlist_id=?", (relativize(hedef), vid, pid))
        n += 1
    conn.commit()
    if n:
        log(f"  [i] {n} 'listeden cikmis' kopya satirina kendi arsiv adi verildi (sabit bag, yer harcamaz)")
    return n


# ---------------------------------------------------------------------------
# KAPI: COKLU SILINME ONAYI.  Cekirdek, kayitli listede sayim ani dusunce ('sayim dustu') cikarma tespitini KENDI KARARIYLA atlar
# ve kullaniciya haber vermezdi.  Artik atlanan (ve baska listede kopyasi olmayan) kayitlar 'silinme_bekleyen'e yazilir;
# kullanici onaylarsa 'removed_from_playlist' olur, reddederse ('koru') bir daha sorulmaz.  Tek yazma kapisi bu uc fonksiyondur.
# ---------------------------------------------------------------------------
def _silinme_tablosu(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS silinme_bekleyen ("
                 "playlist_id INTEGER NOT NULL, video_id TEXT NOT NULL, baslik TEXT, tespit TEXT NOT NULL, "
                 "durum TEXT NOT NULL DEFAULT 'bekliyor', PRIMARY KEY (playlist_id, video_id))")


def silinme_bekleyenler(conn, korunanlar_da=False):
    """Onay bekleyen (ve istenirse 'koru' dedigin) kayitlar: [{playlist_id, playlist_alias, video_id, title, uploader, durum,
    tespit, dosya_var}].  Salt okur."""
    _silinme_tablosu(conn)
    kosul = "" if korunanlar_da else "WHERE s.durum='bekliyor'"
    sonuc = []
    for r in conn.execute(
            "SELECT s.playlist_id, COALESCE(p.alias, p.title) AS alias, s.video_id, COALESCE(v.title, s.baslik) AS title, "
            "v.uploader AS uploader, s.durum, s.tespit, v.file_path AS file_path "
            "FROM silinme_bekleyen s JOIN playlists p ON p.id=s.playlist_id "
            "LEFT JOIN videos v ON v.video_id=s.video_id AND v.playlist_id=s.playlist_id "
            f"{kosul} ORDER BY p.id, v.playlist_index").fetchall():
        yol = resolve_video_path(r["file_path"]) if r["file_path"] else None
        sonuc.append({"playlist_id": r["playlist_id"], "playlist_alias": r["alias"], "video_id": r["video_id"],
                      "title": r["title"], "uploader": r["uploader"], "durum": r["durum"], "tespit": r["tespit"],
                      "dosya_var": bool(yol and yol.exists())})
    return sonuc


def silinmeyi_onayla(conn, anahtarlar, log=print):
    """anahtarlar = [(playlist_id, video_id)]: kullanici 'bunlar gercekten listeden cikarildi' dedi -> 'removed_from_playlist'
    (cekirdegin kendi cikarma sonucuyla ayni; dosya arsive tasinir).  Doner: islenen sayisi."""
    _silinme_tablosu(conn)
    n = 0
    for pid, vid in anahtarlar:
        r = conn.execute("SELECT title FROM videos WHERE video_id=? AND playlist_id=? AND status='active'", (vid, pid)).fetchone()
        if r:
            conn.execute("UPDATE videos SET status='removed_from_playlist' WHERE video_id=? AND playlist_id=?", (vid, pid))
            log_event(conn, "removed_from_playlist", vid, pid, f"{r['title']} (kullanici onayladi)")
            n += 1
        conn.execute("DELETE FROM silinme_bekleyen WHERE playlist_id=? AND video_id=?", (pid, vid))
    conn.commit()
    if n:
        arsive_toparla(conn, log=log)
    return n


def silinmeyi_koru(conn, anahtarlar):
    """anahtarlar = [(playlist_id, video_id)]: kullanici 'silinmedi, kalsin' dedi -> kayit 'active' kalir, video listeye geri
    donene kadar bir daha sorulmaz.  Doner: islenen sayisi."""
    _silinme_tablosu(conn)
    n = 0
    for pid, vid in anahtarlar:
        n += conn.execute("UPDATE silinme_bekleyen SET durum='reddedildi' WHERE playlist_id=? AND video_id=?",
                          (pid, vid)).rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# KAPI: BILINMEYEN DOSYALAR.  Veritabaninda HICBIR kayda bagli olmayan dosyalar (video, yan dosya, ayni dosyanin fazladan
# sabit bag adi) downloads/_Bilinmeyen/ altina toplanir.  '_' ile baslayan her ust klasor gibi _Bilinmeyen de otomatik
# "manuel koleksiyon" olur ve GUI'de "[K] Bilinmeyen" sekmesi acar (sync_manual_collections); _ListeDisi/_YayındanKalkan ise
# ayni yerel klasor kuralinin ozel gosterimli (Liste Disi / Yayindan Kalkan sekmesi) halidir.
# Yazma kapisi bilinmeyenleri_tasi; GUI/CLI yalniz manuel_tarama / tum_klasorleri_tara kontrolorlerini cagirir.
# ---------------------------------------------------------------------------
BILINMEYEN_DIR = "_Bilinmeyen"
BILINMEYEN_EN_COK_ORAN = 0.40        # video dosyalarinin bundan fazlasi 'bagsiz' cikarsa (yanlis DB / klasor eslesmesi?) TASIMAZ
_BILINMEYEN_YOKSAY = {"desktop.ini", "thumbs.db", ".ds_store"}
_BILINMEYEN_GECICI_UZANTI = {".part", ".ytdl", ".temp", ".tmp"}


def _bilinmeyen_plani(conn):
    """YALNIZ OKUR.  Doner: {'dosyalar': [(Path, 'video'|'yan')], 'video_toplam', 'video_bagsiz', 'atlanan_baglanabilir',
    'atlanan_gecici'}.  Bagsiz = hicbir videos.file_path'in gostermedigi dosya; baglanabilir = adindaki [video_id]'nin DB
    kaydi var ama o kaydin dosyasi yok (scan_and_relink_files baglar) -> dokunulmaz; gecici = yarim indirme parcasi."""
    kok = Path(_cached.DOWNLOADS)
    sonuc = {"dosyalar": [], "video_toplam": 0, "video_bagsiz": 0, "atlanan_baglanabilir": 0, "atlanan_gecici": 0}
    if not kok.is_dir():
        return sonuc
    bagli = set()
    dosyasiz_idler = set()
    for r in conn.execute("SELECT video_id, file_path FROM videos").fetchall():
        p = resolve_video_path(r["file_path"]) if r["file_path"] else None
        if p is not None:
            bagli.add(os.path.normcase(str(p)))
        if not (p and p.exists()):
            dosyasiz_idler.add(r["video_id"])
    bagli_govde = {}                    # klasor -> {bagli videolarin govdeleri (kucuk harf)}
    for yol in bagli:
        klasor, ad = os.path.split(yol)
        bagli_govde.setdefault(klasor, set()).add(os.path.splitext(ad)[0])
    for klasor, _alt, dosyalar in os.walk(kok):
        goreli = Path(klasor).relative_to(kok)
        if goreli.parts and (goreli.parts[0] == BILINMEYEN_DIR or goreli.parts[0] in _ARSIV_ADLARI):
            continue                  # hedef klasor + cevrimdisi arsivler (_ListeDisi/_YayındanKalkan) bilerek ayri tutulan yerel klasorler
        govdeler = bagli_govde.get(os.path.normcase(klasor), set())
        for ad in dosyalar:
            if ad.lower() in _BILINMEYEN_YOKSAY:
                continue
            yol = os.path.join(klasor, ad)
            govde, uzanti = os.path.splitext(ad)
            if uzanti.lower() in _BILINMEYEN_GECICI_UZANTI or _PARCA_RE.search(govde) or _PARCA_RE.search(ad):
                sonuc["atlanan_gecici"] += 1
                continue
            video = uzanti.lower() in VIDEO_EXTENSIONS
            if video:
                sonuc["video_toplam"] += 1
                if os.path.normcase(yol) in bagli:
                    continue
                m = _BAG_VID_RE.search(govde)
                if m and m.group(1) in dosyasiz_idler:
                    sonuc["atlanan_baglanabilir"] += 1
                    continue
                sonuc["video_bagsiz"] += 1
                sonuc["dosyalar"].append((Path(yol), "video"))
            else:
                kucuk = ad.lower()
                if any(kucuk.startswith(g + ".") for g in govdeler):
                    continue                       # bagli bir videonun yan dosyasi
                sonuc["dosyalar"].append((Path(yol), "yan"))
    return sonuc


def bilinmeyenleri_tasi(conn, log=print, kuru=False, zorla=False):
    """Veritabaninda bagi olmayan TUM dosyalari downloads/_Bilinmeyen/ altina tasir (silmez).  Doner: ozet sozlugu
    {'tasinan_video','tasinan_yan','atlanan_baglanabilir','atlanan_gecici','durdu','dosyalar'}.  kuru=True: yalniz plan.
    Koruma: bagsiz video orani BILINMEYEN_EN_COK_ORAN'i (ve 20 dosyayi) asarsa TASIMAZ (zorla=True ile asilir)."""
    plan = _bilinmeyen_plani(conn)
    ozet = {"tasinan_video": 0, "tasinan_yan": 0, "atlanan_baglanabilir": plan["atlanan_baglanabilir"],
            "atlanan_gecici": plan["atlanan_gecici"], "durdu": None, "dosyalar": plan["dosyalar"]}
    if not plan["dosyalar"]:
        return ozet
    if not zorla and plan["video_bagsiz"] > 20 and plan["video_bagsiz"] > BILINMEYEN_EN_COK_ORAN * max(1, plan["video_toplam"]):
        ozet["durdu"] = (f"{plan['video_bagsiz']}/{plan['video_toplam']} video bagsiz gorunuyor (> %{int(BILINMEYEN_EN_COK_ORAN * 100)}): "
                         f"yanlis veritabani/klasor eslesmesi olabilir, tasinmadi")
        log(f"  [!] BILINMEYEN: {ozet['durdu']}")
        return ozet
    if kuru:
        return ozet
    kok = Path(_cached.DOWNLOADS)
    hedef_kok = kok / BILINMEYEN_DIR
    hedef_kok.mkdir(parents=True, exist_ok=True)
    bosalan = set()
    for yol, tur in plan["dosyalar"]:
        hedef = hedef_kok / yol.name
        n = 2
        while hedef.exists():
            hedef = hedef_kok / f"{yol.stem} ({n}){yol.suffix}"
            n += 1
        try:
            os.replace(yol, hedef)
        except OSError as e:
            log(f"  [!] bilinmeyen dosya tasinamadi ({yol.name[:50]}): {e}")
            continue
        bosalan.add(yol.parent)
        if tur == "video":
            ozet["tasinan_video"] += 1
            m = _BAG_VID_RE.search(yol.stem)
            log_event(conn, "moved_to_bilinmeyen", m.group(1) if m else None, None,
                      f"{relativize(yol)} -> {relativize(hedef)}")
            log(f"  [?] bilinmeyen: {relativize(yol)} -> {BILINMEYEN_DIR}/")
        else:
            ozet["tasinan_yan"] += 1
    conn.commit()
    bilinen = {(r["alias"] or "").lower() for r in conn.execute("SELECT alias FROM playlists").fetchall()}
    for k in sorted(bosalan, key=lambda x: len(x.parts), reverse=True):      # ic klasorden dise
        try:
            goreli = k.relative_to(kok)
            ust = goreli.parts[0] if goreli.parts else ""
            if goreli.parts and not ust.startswith("_") and ust.lower() not in bilinen and not any(k.iterdir()):
                k.rmdir()
        except (OSError, ValueError):
            pass
    if ozet["tasinan_video"] or ozet["tasinan_yan"]:
        log(f"  [?] {ozet['tasinan_video']} video + {ozet['tasinan_yan']} yan dosya {BILINMEYEN_DIR}/ altina tasindi")
    return ozet


def manuel_tarama(conn, log=print):
    """'Manuel klasorleri tara' kontrolorunun TEK kapisi: '_' klasorleri koleksiyon olarak oku -> bagsiz dosyalari _Bilinmeyen'e
    tasi -> tasindiysa koleksiyonlari bir kez daha oku (yeni _Bilinmeyen sekmesi guncel olsun)."""
    ozet = sync_manual_collections(conn, log=log)
    b = bilinmeyenleri_tasi(conn, log=log)
    if b["tasinan_video"] or b["tasinan_yan"]:
        ikinci = sync_manual_collections(conn, log=log)
        ozet["new_files"] = ozet.get("new_files", 0) + ikinci.get("new_files", 0)
    ozet["bilinmeyen"] = {k: v for k, v in b.items() if k != "dosyalar"}
    return ozet


def tum_klasorleri_tara(conn, log=print):
    """'Tum klasorleri tara' kontrolorunun TEK kapisi: kayip kayitlari yeniden bagla -> bagsiz dosyalari _Bilinmeyen'e tasi ->
    klasor uzlastirma -> (uzlastirma bir sey tasidiysa) tekrar bagsizlari tasi -> koleksiyonlari guncelle."""
    s = scan_and_relink_files(conn, log=log)
    b1 = bilinmeyenleri_tasi(conn, log=log)
    r = reconcile_download_folders(conn, log=log)
    b2 = bilinmeyenleri_tasi(conn, log=log) if (r.get("orphans") or r.get("duplicates")) else {"tasinan_video": 0, "tasinan_yan": 0}
    s.update({f"folder_{k}": v for k, v in r.items()})
    s["bilinmeyen"] = {"tasinan_video": b1["tasinan_video"] + b2["tasinan_video"],
                       "tasinan_yan": b1["tasinan_yan"] + b2["tasinan_yan"],
                       "atlanan_baglanabilir": b1["atlanan_baglanabilir"], "durdu": b1.get("durdu")}
    if s["bilinmeyen"]["tasinan_video"] or s["bilinmeyen"]["tasinan_yan"]:
        sync_manual_collections(conn, log=log)
    return s


# ---------------------------------------------------------------------------
# KAPI 4: YARIM INDIRMEYI SURDUR.
# Diskte yalniz parca (.fNNN / .temp) kalmis, bitmis dosyasi olmayan videolar bulunur
# (yarim_indirmeler) ve ayni parcalarin format kimlikleri zorlanarak cekirdegin KENDI
# download_video'su ile tamamlanir (yarim_indirmeyi_surdur).  Boylece 1.9 GB'lik gorunum
# parcasi yeniden inmez; yt-dlp var olan .fNNN dosyalarini "zaten indirilmis" sayar.
# ---------------------------------------------------------------------------
_SES_ITAG = {139, 140, 141, 171, 172, 249, 250, 251, 256, 258, 325, 328, 338, 380, 599, 600}
_PARCA_ID_RE = re.compile(r"\.f(\d{2,4})$")


def yarim_format(parca_idleri):
    """Mevcut parca kimliklerinden yt-dlp format secicisi: 137+ba, bv*+251, 401+251 ..."""
    ses = [i for i in parca_idleri if i in _SES_ITAG]
    goruntu = [i for i in parca_idleri if i not in _SES_ITAG]
    v = str(goruntu[0]) if goruntu else "bv*"
    a = str(ses[0]) if ses else "ba"
    return f"{v}+{a}/bv*+ba/b"


def yarim_indirmeler(conn):
    """[{video_id, klasor, index, idler, playlist_id}] - yalniz parcasi olan, bitmis kopyasi OLMAYAN videolar."""
    if not _cached.DOWNLOADS.exists():
        return []
    gruplar = {}
    for f in _cached.DOWNLOADS.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTENSIONS or not _PARCA_RE.search(f.stem):
            continue
        if _cached.REMOVED_SUBDIR in f.parts or _cached.INCELEME_SUBDIR in f.parts:
            continue
        vid = _extract_video_id_orig(f.stem)
        if not vid:
            continue
        g = gruplar.setdefault((f.parent, vid), {"adlar": [], "idler": set()})
        g["adlar"].append(f.name)
        m = _PARCA_ID_RE.search(f.stem)
        if m:
            g["idler"].add(int(m.group(1)))
    tam = _cached._build_disk_index()            # parca dosyalari gormez -> yalniz bitmis kopyalar
    sonuc = []
    for (klasor, vid), g in sorted(gruplar.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        if vid in tam:
            continue                              # bitmis kopyasi var: bu yarim indirme degil, artik
        mi = re.match(r"(\d{3})\s*-", sorted(g["adlar"])[0])
        pid = None
        for r in conn.execute("SELECT v.playlist_id, p.alias FROM videos v JOIN playlists p ON p.id=v.playlist_id "
                              "WHERE v.video_id=?", (vid,)).fetchall():
            if r[1] == klasor.name:
                pid = r[0]
        sonuc.append({"video_id": vid, "klasor": klasor, "index": int(mi.group(1)) if mi else None,
                      "idler": sorted(g["idler"]), "playlist_id": pid})
    return sonuc


def yarim_indirmeyi_surdur(conn, oge, log=print):
    """Doner: (basari, mesaj).  Basarida (kaydi varsa) file_path bitmis dosyaya cekilir."""
    vid = oge["video_id"]
    zorla = yarim_format(oge["idler"])
    cls = globals().get("_CappedYoutubeDL")
    if cls is not None:
        cls.FORMAT_ZORLA = zorla
    try:
        yol = download_video(f"https://www.youtube.com/watch?v={vid}", oge["klasor"], oge["index"])
    finally:
        if cls is not None:
            cls.FORMAT_ZORLA = None
    if not yol:
        return False, "indirilemedi"
    p = Path(yol)
    if not p.is_file() or _PARCA_RE.search(p.stem):
        return False, f"tamamlanmis dosya yok: {p}"
    if oge["playlist_id"] is not None:
        conn.execute("UPDATE videos SET file_path=?, downloaded_at=? WHERE video_id=? AND playlist_id=?",
                     (relativize(p), now_iso(), vid, oge["playlist_id"]))
        log_event(conn, "downloaded", vid, oge["playlist_id"], "devam ettirildi: " + relativize(p))
        conn.commit()
    return True, str(p)


# ---------------------------------------------------------------------------
# KAPI 5: YAYINDAN KALKAN VIDEOLAR -> <playlist>/_YayındanKalkan/.
# Sorun: yayindan kalkmis (unavailable) bir videonun yerel dosyasi playlist klasorunde durunca
# cekirdek her taramada kaydi 'active'e cevirip yeniden "YAYINDAN KALKMIS" diye listeler.
# Cozum: dosya ilk taramada <playlist>/_YayındanKalkan/ altina tasinir; cekirdegin
# "REMOVED_SUBDIR in yol.parts" kontrolleri bu klasoru de arsiv (_silinenler gibi) sayar,
# boylece kayit unavailable kalir ve tekrar listelenmez.  Tasima yalniz sync_two_phase'in
# 2b asamasindan yapilir (yayindan_kalkanlari_tasi tek kapi).
# ---------------------------------------------------------------------------
YAYINDAN_KALKAN_DIR = "_YayındanKalkan"     # YouTube'un yaptigi: video YouTube'da artik erisilemiyor
LISTE_DISI_DIR = "_ListeDisi"                # SENIN yaptigin: video YouTube'da var, sen listeden cikardin
ESKI_LISTE_DISI_DIR = "_silinenler"          # LISTE_DISI_DIR'in eski adi (gecis suresince hala taninir)


class _ArsivKlasoru(str):
    """REMOVED_SUBDIR yerine gecer.  Degeri '_ListeDisi' (cekirdek yeni tasimalari buraya yapar) ama
    '_YayındanKalkan' ve eski '_silinenler' ile de esit sayilir: ucu de 'arsiv klasoru'dur ve icindeki dosyali
    kayit taramada 'active'e cevrilmez.  Iki kavram KLASORDE ayri kalir (LISTE_DISI_DIR / YAYINDAN_KALKAN_DIR)."""

    def __eq__(self, other):
        return str.__eq__(self, other) is True or (isinstance(other, str) and other in (YAYINDAN_KALKAN_DIR, ESKI_LISTE_DISI_DIR))

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = str.__hash__


_cached.REMOVED_SUBDIR = REMOVED_SUBDIR = _ArsivKlasoru(LISTE_DISI_DIR)


def liste_disi_klasorlerini_tasi(conn, log=print):
    """Eski '_silinenler' klasorlerini '_ListeDisi'ya tasir (dosyalar + DB yollari).  Tekrar calistirilabilir.
    Hedefte ayni adli dosya varsa dokunmaz.  Doner: tasinan dosya sayisi."""
    kok = _cached.DOWNLOADS
    n = 0
    if kok.exists():
        for eski in sorted(p for p in kok.rglob(ESKI_LISTE_DISI_DIR) if p.is_dir()):
            yeni = eski.parent / LISTE_DISI_DIR
            yeni.mkdir(exist_ok=True)
            for f in sorted(eski.iterdir()):
                if not f.is_file():
                    continue
                hedef = yeni / f.name
                if hedef.exists():
                    log(f"  [!] {LISTE_DISI_DIR}/ icinde ayni ad var, tasinmadi: {f}")
                    continue
                f.replace(hedef)
                n += 1
            try:
                eski.rmdir()
            except OSError:
                pass                                  # icinde baska sey kaldiysa birak
    guncel = 0
    for vid, pid, fp in conn.execute("SELECT video_id, playlist_id, file_path FROM videos "
                                     "WHERE file_path IS NOT NULL").fetchall():
        parcalar = fp.replace("/", "\\").split("\\")
        if ESKI_LISTE_DISI_DIR not in parcalar:
            continue
        yeni_fp = "\\".join(LISTE_DISI_DIR if x == ESKI_LISTE_DISI_DIR else x for x in parcalar)
        p = resolve_video_path(yeni_fp)
        eski_p = resolve_video_path(fp)
        # yolu YALNIZ dosya eski yerinden gidip yenide durunca degistir (cakisma: eski yerde kaldiysa dokunma)
        if p and p.exists() and not (eski_p and eski_p.exists()):
            conn.execute("UPDATE videos SET file_path=? WHERE video_id=? AND playlist_id=?", (yeni_fp, vid, pid))
            guncel += 1
    conn.commit()
    if n or guncel:
        log(f"  [>] eski klasor adi ({ESKI_LISTE_DISI_DIR}) -> {LISTE_DISI_DIR}: {n} dosya tasindi, {guncel} kayit guncellendi")
    return n


# --- YouTube durumu onbellegi: LISTE DISI (senin cikardigin) videolar YouTube'da hala var mi? ---
# Iki kavram karismasin diye ayri tutulur: durum 'removed_from_playlist' SENIN eylemin; YouTube'da da
# kalkmis olup olmadigi yalnizca bilgi olarak youtube_durum tablosunda durur (dosya/durum degismez).
YOUTUBE_KONTROL_GUN = 14
YOUTUBE_KONTROL_ESIK = 30


def youtube_durumlari(conn):
    """{video_id: 'yayinda' | 'kalkmis'} - liste disi videolarin son bilinen YouTube durumu."""
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT video_id, durum FROM youtube_durum").fetchall()}
    except _sqlite3.Error:
        return {}


def liste_disi_youtube_durumunu_yenile(conn, log=print):
    """removed_from_playlist videolarini yt-dlp ile yoklar (en cok YOUTUBE_KONTROL_ESIK, KONTROL_GUN gunde bir).
    Doner: yoklanan video sayisi.  Dogrulanamayanlar ('belirsiz') kaydedilmez, sonra tekrar denenir."""
    conn.execute("CREATE TABLE IF NOT EXISTS youtube_durum (video_id TEXT PRIMARY KEY, durum TEXT NOT NULL, kontrol TEXT NOT NULL)")
    simdi = now_iso()
    n = 0
    for (vid,) in conn.execute("SELECT DISTINCT video_id FROM videos WHERE status='removed_from_playlist'").fetchall():
        eski = conn.execute("SELECT kontrol FROM youtube_durum WHERE video_id=?", (vid,)).fetchone()
        if eski:
            try:
                gun = (_datetime.fromisoformat(simdi) - _datetime.fromisoformat(eski[0])).days
            except ValueError:
                gun = YOUTUBE_KONTROL_GUN
            if gun < YOUTUBE_KONTROL_GUN:
                continue
        if n >= YOUTUBE_KONTROL_ESIK:
            break
        durum, _ = _video_durumu(vid)
        if durum == "belirsiz":
            continue
        _youtube_durum_yaz(conn, vid, "kalkmis" if durum == "kalkmis" else "yayinda", simdi)
        n += 1
        if durum == "kalkmis":
            log(f"  [i] liste disi video YouTube'da da kalkmis: {vid}")
    conn.commit()
    return n


def _youtube_durum_yaz(conn, video_id, durum, simdi=None):
    """youtube_durum tablosuna TEK yazma kapisi (commit cagirana ait).  durum: 'yayinda' | 'kalkmis'."""
    conn.execute("CREATE TABLE IF NOT EXISTS youtube_durum (video_id TEXT PRIMARY KEY, durum TEXT NOT NULL, kontrol TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO youtube_durum (video_id, durum, kontrol) VALUES (?,?,?)",
                 (video_id, durum, simdi or now_iso()))


def youtube_durum_kayitlari(conn):
    """{video_id: (durum, kontrol_zamani)} - son bilinen YouTube durumu (salt okunur)."""
    try:
        return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT video_id, durum, kontrol FROM youtube_durum").fetchall()}
    except _sqlite3.Error:
        return {}


YAYINDAN_KALKAN_YOKLAMA_BEKLEME = (1.0, 2.5)      # yoklamalar arasi sn (bot kontrolunu tetiklememek icin)
YAYINDAN_KALKAN_ART_ARDA_BELIRSIZ = 3             # bu kadar ust uste dogrulanamazsa (bot kontrolu/ag) yoklama durur


def yayindan_kalkanlari_yokla(conn, video_idleri=None, log=print, should_cancel=None, bekle=None):
    """'Yayindan Kalkan' (status='unavailable') videolari gercekten YouTube'da yayinda mi diye yt-dlp ile yoklar.
    video_idleri verilirse yalniz onlari (yine yalniz unavailable olanlar).  Sonuc youtube_durum'a yazilir
    ('yayinda' | 'kalkmis'); dogrulanamayan ('belirsiz': ag, yas siniri, bot kontrolu) KAYDEDILMEZ.
    Hicbir video/dosya/durum degismez - yalniz bilgi.  Doner: {yayinda, kalkmis, belirsiz, iptal, geri_gelen:[(id, baslik)]}."""
    import random as _random
    import time as _time
    bekle = YAYINDAN_KALKAN_YOKLAMA_BEKLEME if bekle is None else bekle
    istenen = set(video_idleri) if video_idleri is not None else None
    satirlar = conn.execute("SELECT video_id, MAX(title) FROM videos WHERE status='unavailable' "
                            "GROUP BY video_id ORDER BY video_id").fetchall()
    sonuc = {"yayinda": 0, "kalkmis": 0, "belirsiz": 0, "iptal": False, "geri_gelen": []}
    ust_uste = 0
    ilk = True
    for vid, baslik in satirlar:
        if istenen is not None and vid not in istenen:
            continue
        if should_cancel and should_cancel():
            sonuc["iptal"] = True
            break
        if not ilk and bekle and bekle[1] > 0:
            _time.sleep(_random.uniform(*bekle))
        ilk = False
        durum, ayrinti = _video_durumu(vid)
        if durum == "belirsiz":
            sonuc["belirsiz"] += 1
            ust_uste += 1
            log(f"  [?] {vid}: dogrulanamadi ({str(ayrinti)[:90]})")
            if ust_uste >= YAYINDAN_KALKAN_ART_ARDA_BELIRSIZ:
                log("  [!] ust uste dogrulanamadi (bot kontrolu/ag olabilir) - yoklama durduruldu, sonra tekrar dene")
                break
            continue
        ust_uste = 0
        _youtube_durum_yaz(conn, vid, "yayinda" if durum == "ok" else "kalkmis")
        conn.commit()
        if durum == "ok":
            sonuc["yayinda"] += 1
            sonuc["geri_gelen"].append((vid, baslik))
            log(f"  [!] YouTube'da YAYINDA: {(baslik or '')[:60]} [{vid}]")
        else:
            sonuc["kalkmis"] += 1
    return sonuc


# --- UST DUZEY ARSIV: YouTube'da olmayan videolar playlist klasorunun ALTINDA degil, downloads\ altinda tek yerde
# birikir (cevrimdisi arsiv):  downloads\_YayındanKalkan\  (YouTube kaldirdi)  ve  downloads\_ListeDisi\  (sen listeden cikardin).
# Dosya adlari duz (alt klasorsuz) durur; ad zaten 'NNN - baslik [video_id].uzanti' oldugundan cakisma beklenmez,
# cakisirsa dosya yerinde birakilir ve loglanir.  Cekirdegin move_to_removed / move_back_from_removed'i klasor
# yapisina baglidir (parent / REMOVED_SUBDIR, parent.parent): asagida bu iki kapi ust duzeye uyarlanir.
import os

_ARSIV_DURUM_KLASORU = {"unavailable": YAYINDAN_KALKAN_DIR, "removed_from_playlist": LISTE_DISI_DIR}
_ARSIV_ADLARI = (LISTE_DISI_DIR, YAYINDAN_KALKAN_DIR, ESKI_LISTE_DISI_DIR)


def arsiv_kok(durum_ya_da_klasor):
    """Ust duzey arsiv klasoru: durum ('unavailable' | 'removed_from_playlist') ya da klasor adi verilir."""
    ad = _ARSIV_DURUM_KLASORU.get(durum_ya_da_klasor, durum_ya_da_klasor)
    return _cached.DOWNLOADS / ad


def _ust_arsivde(yol):
    """Dosya DOGRUDAN downloads\\<arsiv klasoru>\\ icinde mi?"""
    p = Path(yol)
    return p.parent.parent == _cached.DOWNLOADS and p.parent.name in _ARSIV_ADLARI


_move_to_removed_orig = _cached.move_to_removed
_move_back_from_removed_orig = _cached.move_back_from_removed


def move_to_removed(abs_video_path):
    """Cekirdegin 'listeden cikan videoyu arsive tasi' kapisi: hedef downloads\\_ListeDisi\\ (playlist alti DEGIL)."""
    p = Path(abs_video_path)
    if not p.exists() or _ust_arsivde(p):
        return abs_video_path
    return _cached._move_files_with_sidecars(p, arsiv_kok(LISTE_DISI_DIR))


def _sahip_playlist_klasoru(abs_video_path):
    """Ust duzey arsivdeki dosyanin sahibi olan kaydin playlist klasoru (geri tasima hedefi); bulunamazsa None."""
    import sqlite3
    vid = _extract_video_id_orig(Path(abs_video_path).stem)
    if not vid:
        return None
    hedef = os.path.normcase(str(abs_video_path))
    con = sqlite3.connect(str(_cached.DB_PATH))
    try:
        for pid, alias, baslik, fp in con.execute(
                "SELECT v.playlist_id, p.alias, p.title, v.file_path FROM videos v "
                "JOIN playlists p ON p.id=v.playlist_id WHERE v.video_id=? AND v.file_path IS NOT NULL", (vid,)):
            yol = resolve_video_path(fp)
            if yol and os.path.normcase(str(yol)) == hedef:
                return _cached.DOWNLOADS / sanitize(alias or baslik or f"playlist_{pid}")
    finally:
        con.close()
    return None


def move_back_from_removed(abs_video_path):
    """Video listeye geri dondu: ust duzey arsivden SAHIBI olan kaydin playlist klasorune tasir (sahip bulunamazsa
    dosyaya dokunmaz).  Eski bicim (<playlist>\\_ListeDisi\\) icin cekirdegin kendi mantigi."""
    p = Path(abs_video_path)
    if not p.exists():
        return abs_video_path
    if _ust_arsivde(p):
        hedef = _sahip_playlist_klasoru(p)
        return _cached._move_files_with_sidecars(p, hedef) if hedef else abs_video_path
    return _move_back_from_removed_orig(abs_video_path)


_cached.move_to_removed = move_to_removed
_cached.move_back_from_removed = move_back_from_removed


def arsive_toparla(conn, log=print, durumlar=("unavailable", "removed_from_playlist")):
    """Arsiv durumundaki (yayindan kalkan / listeden cikan) kayitlarin yerel dosyalarini + yan dosyalarini
    downloads\\_YayındanKalkan\\ / downloads\\_ListeDisi\\ altina toplar ve DB yolunu gunceller.  Zaten orada olana,
    baska bir kaydin da dosyasi olana (paylasilan) dokunmaz.  Tekrar calistirilabilir.
    Doner: {durum: tasinan_kayit_sayisi}."""
    sonuc = {d: 0 for d in durumlar}
    for durum in durumlar:
        olay = "moved_to_yayindan_kalkan" if durum == "unavailable" else "moved_to_liste_disi"
        hedef_klasor = arsiv_kok(durum)
        for vid, pid, title, fp in conn.execute(
                "SELECT video_id, playlist_id, title, file_path FROM videos "
                "WHERE status=? AND file_path IS NOT NULL", (durum,)).fetchall():
            src = resolve_video_path(fp)
            if not src or not src.exists() or src.parent == hedef_klasor or _cached.INCELEME_SUBDIR in src.parts:
                continue
            if conn.execute("SELECT 1 FROM videos WHERE file_path=? AND NOT (video_id=? AND playlist_id=?) LIMIT 1",
                            (fp, vid, pid)).fetchone():
                log(f"  [!] arsiv dosyasi baska kayitla paylasiliyor, tasinmadi: {str(title or '')[:50]}")
                continue
            yeni = _cached._move_files_with_sidecars(src, hedef_klasor)
            if Path(yeni) == src:
                log(f"  [!] {hedef_klasor.name}/ icinde ayni ad var, tasinmadi: {src.name[:60]}")
                continue
            conn.execute("UPDATE videos SET file_path=? WHERE video_id=? AND playlist_id=?",
                         (relativize(yeni), vid, pid))
            log_event(conn, olay, vid, pid, relativize(yeni))
            log(f"  [>] {hedef_klasor.name}/'a tasindi: {str(title or '')[:60]}")
            sonuc[durum] += 1
    conn.commit()
    _bos_arsiv_alt_klasorlerini_sil()
    return sonuc


def yayindan_kalkanlari_tasi(conn, log=print):
    """Geriye uyumlu ad: yalniz yayindan kalkanlari downloads\\_YayındanKalkan\\ altina toplar.  Doner: tasinan sayisi."""
    return arsive_toparla(conn, log=log, durumlar=("unavailable",))["unavailable"]


def _bos_arsiv_alt_klasorlerini_sil():
    """<playlist>\\_ListeDisi / _YayındanKalkan / _silinenler alt klasorleri bosaldiysa kaldirir (bos degilse dokunmaz)."""
    kok = _cached.DOWNLOADS
    if not kok.exists():
        return 0
    n = 0
    for ust in [p for p in kok.iterdir() if p.is_dir() and p.name not in _ARSIV_ADLARI]:
        for ad in _ARSIV_ADLARI:
            alt = ust / ad
            if alt.is_dir():
                try:
                    alt.rmdir()
                    n += 1
                except OSError:
                    pass                              # bos degil
    return n


def fazla_arsiv_adlarini_temizle(conn, log=print):
    """<playlist>\\<arsiv klasoru>\\ altindaki 'fazladan ad'lari siler: video adi hicbir DB kaydinin yolu DEGIL, ama
    AYNI fiziksel dosya (sabit bag) bir DB kaydinca baska adla kullaniliyor => veri kaybi yok.  Yan dosya, kullanilan
    dosyanin yaninda ICERIGI (SHA-256) AYNI esi varsa silinir; yoksa dokunulmaz.  Doner: silinen dosya sayisi."""
    kullanilan_yol, kullanilan_inode = set(), {}
    for (fp,) in conn.execute("SELECT file_path FROM videos WHERE file_path IS NOT NULL").fetchall():
        p = resolve_video_path(fp)
        try:
            st = os.stat(p) if p else None
        except OSError:
            st = None
        if st:
            kullanilan_yol.add(os.path.normcase(str(p)))
            kullanilan_inode.setdefault((st.st_dev, st.st_ino), p)
    kok = _cached.DOWNLOADS
    if not kok.exists():
        return 0
    silinen = 0
    for ust in [p for p in kok.iterdir() if p.is_dir() and p.name not in _ARSIV_ADLARI]:
        for ad in _ARSIV_ADLARI:
            alt = ust / ad
            if not alt.is_dir():
                continue
            for f in sorted(alt.iterdir()):
                if not f.is_file() or f.suffix.lower() not in VIDEO_EXTENSIONS or _PARCA_RE.search(f.stem):
                    continue
                if os.path.normcase(str(f)) in kullanilan_yol:
                    continue                          # bir kaydin gercek dosyasi: dokunma
                st = os.stat(f)
                asil = kullanilan_inode.get((st.st_dev, st.st_ino))
                if asil is None or st.st_nlink < 2 or asil == f:
                    continue                          # baska adi yok / kullanilmiyor: fazladan ad degil
                for yan in sorted(alt.iterdir()):
                    if yan == f or not yan.is_file() or yan.suffix.lower() in VIDEO_EXTENSIONS \
                            or not yan.name.startswith(f.stem + "."):
                        continue
                    es = asil.parent / (asil.stem + yan.name[len(f.stem):])
                    if es.is_file() and es.stat().st_size == yan.stat().st_size and _ozet_sha256(es) == _ozet_sha256(yan):
                        yan.unlink()
                        silinen += 1
                f.unlink()                            # asil dosya baska adla duruyor: yalniz bu AD gider
                silinen += 1
                log(f"  [-] fazladan ad silindi (dosya baska adla duruyor): {ad}/{f.name[:60]}")
    _bos_arsiv_alt_klasorlerini_sil()
    return silinen


_ES_GURULTU = {"dj", "new", "eurodance", "official", "video", "music", "remix", "mix", "hit", "hits", "banger", "feat",
               "ft", "classic", "club", "mega", "megamix", "extended", "version", "edit", "radio", "top", "hot", "super",
               "the", "a", "and", "x", "of", "in", "on", "to", "my", "me", "you", "party", "dance", "edm", "disco",
               "modern", "italo", "euro"}
_ES_YIL_RE = re.compile(r"20\d\d|\d{2}s|90s|vol\d*")
_ES_KLASOR_ETIKETI = {"active": "aktif", "removed_from_playlist": "liste disi", "unavailable": "yayindan kalkan"}


def _video_anahtari(baslik):
    return " ".join(w for w in re.findall(r"[a-z0-9]+", (baslik or "").lower())
                    if w not in _ES_GURULTU and not _ES_YIL_RE.fullmatch(w))


def _es_bul(r, adaylar):
    """Yayindan kalkan kaydin ayni videosu baska yerde var mi?  Doner: {..., 'guc': 'ayni'|'guclu'|'olasi'} | None.
    ayni = ayni video_id baska listede; guclu = sure farki <=3 sn ve baslik benzerligi >=0.75;
    olasi = sure farki <=3 sn ve benzerlik >=0.55 (insan kontrol etmeli)."""
    anahtar = _video_anahtari(r["title"])
    en_iyi, en_iyi_puan = None, None
    for a in adaylar:
        if a["video_id"] == r["video_id"] and a["playlist_id"] == r["playlist_id"]:
            continue
        if a["video_id"] == r["video_id"]:
            guc, skor = "ayni", 1.0
        else:
            if not (r["duration"] and a["duration"]) or abs(r["duration"] - a["duration"]) > 3 or not anahtar:
                continue
            skor = difflib.SequenceMatcher(None, anahtar, _video_anahtari(a["title"])).ratio()
            guc = "guclu" if skor >= 0.75 else ("olasi" if skor >= 0.55 else None)
            if guc is None:
                continue
        fark = 0 if guc == "ayni" else abs(r["duration"] - a["duration"])
        puan = ({"ayni": 3, "guclu": 2, "olasi": 1}[guc], skor, -fark)
        if en_iyi_puan is None or puan > en_iyi_puan:
            en_iyi, en_iyi_puan = {**a, "guc": guc, "skor": round(skor, 2)}, puan
    return en_iyi


def _surum_kelimeleri(baslik):
    """Baslikta parantez/koseli icerigi (remix sahibi, 'Official Video' ...) ve gurultu sozcukleri atilir; sanatci/video sozcukleri kalir."""
    b = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", baslik or "")
    return set(_video_anahtari(b).split()) - {"x"}


def _surum_basligi_benzer(a, b):
    ka, kb = _surum_kelimeleri(a["title"]), _surum_kelimeleri(b["title"])
    return len(ka) >= 2 and len(kb) >= 2 and len(ka & kb) / len(ka | kb) >= 0.7


SURUM_SURE_FARKI = 90      # sn: ayni videonun farkli kesim/remix surumleri arasinda izin verilen en buyuk sure farki


def _ses_onbellek_benzerlik(conn, pa, pb):
    """Iki dosya icin ses_eslesme'de KAYITLI benzerlik (yoksa / boyut degistiyse None).  YALNIZ OKUR."""
    try:
        ba, bb = pa.stat().st_size, pb.stat().st_size
    except OSError:
        return None
    a, b = _ses_cifti((_ses_kimlik(pa, ba), pa, ba), (_ses_kimlik(pb, bb), pb, bb))
    r = conn.execute("SELECT boyut_a, boyut_b, benzerlik FROM ses_eslesme WHERE kimlik_a=? AND kimlik_b=?",
                     (a[0], b[0])).fetchone()
    return r[2] if r and r[0] == a[2] and r[1] == b[2] else None


def _es_gruplari_ekle(conn, result, include_inactive, surum_adaylari=False):
    """'Ayni Videolar' sonucuna, BASLIGI FARKLI ama ayni video olan guclu eslesmeleri (sure farki <=3 sn ve baslik
    benzerligi >=0.75) 'fuzzy' grubu olarak ekler.  Eslestirme TUM klasorleri kapsar: aktif, _ListeDisi ve
    _YayındanKalkan dosyalari (include_inactive kapaliysa yalniz aktifler).  Sure bilgisi olmayan kayit eslenemez."""
    sorgu = ("SELECT v.video_id, v.playlist_id, v.title, v.uploader, v.duration, v.status, v.file_path, "
             "v.playlist_index, p.alias, p.title, p.url FROM videos v JOIN playlists p ON p.id=v.playlist_id "
             "WHERE v.file_path IS NOT NULL")
    if not include_inactive:
        sorgu += " AND v.status='active'"
    satirlar = []
    for r in conn.execute(sorgu).fetchall():
        if r[0] in DUPLICATE_EXCEPTIONS or not r[4]:
            continue
        p = resolve_video_path(r[6])
        if p and p.exists():
            satirlar.append({"video_id": r[0], "playlist_id": r[1], "title": r[2], "uploader": r[3], "duration": r[4],
                             "status": r[5], "file_path": r[6], "playlist_index": r[7],
                             "playlist_alias": (r[8] or "") + {"removed_from_playlist": " [liste disi]",
                                                                "unavailable": " [yayindan kalkan]"}.get(r[5], ""),
                             "playlist_title": r[9], "playlist_url": r[10], "_k": _video_anahtari(r[2])})
    satirlar.sort(key=lambda x: x["duration"])
    ata = {}

    def kok(x):
        while ata.get(x, x) != x:
            ata[x] = ata.get(ata[x], ata[x])
            x = ata[x]
        return x

    for i, a in enumerate(satirlar):
        if not a["_k"]:
            continue
        for b in satirlar[i + 1:]:
            if b["duration"] - a["duration"] > 3:
                break
            if b["video_id"] == a["video_id"] or not b["_k"]:
                continue
            if difflib.SequenceMatcher(None, a["_k"], b["_k"]).ratio() >= 0.75:
                ata[kok(a["video_id"])] = kok(b["video_id"])
    # SURUM ESLESMESI: ayni videonun farkli kesim/remix surumu (sure farki <=SURUM_SURE_FARKI, eski 3 sn kurali tutmayanlar; baslikta sozcuk kumesi
    # >=0.75 ortak).  Baslik+sure tek basina yetmez (SAVAGE-44 serisi gibi farkli videolar ayni sozcukleri paylasir): ses
    # karari sart.  surum_adaylari=True (ses_analizi'ne verilen HAM gruplar) -> aday cift gruba girer ki ses karsilastirilsin;
    # False (GUI'de gorunen) -> yalniz ses_eslesme'de KAYITLI benzerligi >= SES_BENZER_ESIK olanlar (ses_ile_ayir ayrica boler).
    ses_var = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ses_eslesme'").fetchone())
    yol = {}
    for i, a in enumerate(satirlar):
        for b in satirlar[i + 1:]:
            fark = b["duration"] - a["duration"]
            if fark > SURUM_SURE_FARKI:
                break
            if b["video_id"] == a["video_id"] or kok(a["video_id"]) == kok(b["video_id"]):
                continue
            if not _surum_basligi_benzer(a, b):
                continue
            if not surum_adaylari:
                if not ses_var:
                    continue
                for x in (a, b):
                    if x["video_id"] not in yol:
                        yol[x["video_id"]] = resolve_video_path(x["file_path"])
                pa, pb = yol[a["video_id"]], yol[b["video_id"]]
                bz = _ses_onbellek_benzerlik(conn, pa, pb) if pa and pb else None
                if bz is None or bz < SES_BENZER_ESIK:
                    continue
            ata[kok(a["video_id"])] = kok(b["video_id"])
    kume = {}
    for s in satirlar:
        kume.setdefault(kok(s["video_id"]), []).append(s)
    fuzzy = result.setdefault("fuzzy", {})
    mevcut = [{e.get("video_id") for e in v} for v in fuzzy.values()]
    for uyeler in kume.values():
        idler = {u["video_id"] for u in uyeler}
        if len(idler) < 2 or any(idler <= m for m in mevcut):
            continue
        anahtar = "~ " + (uyeler[0]["_k"] or uyeler[0]["title"] or "")
        while anahtar in fuzzy:
            anahtar += "'"
        fuzzy[anahtar] = [{k: v for k, v in u.items() if k != "_k"} for u in uyeler]


def yayindan_kalkanlar(conn):
    """GUI 'Yayindan Kalkan' sekmesinin TEK okuma kapisi: status='unavailable' kayitlari (dict listesi).
    'kalkis' = ilk 'became_unavailable' olayinin zamani; 'boyut' = yerel dosya bayti; 'es' = ayni videonun
    canli baska kopyasi (bkz. _es_bul) ya da None."""
    kalkan = _yayindan_kalkan_satirlari(conn)
    adaylar = []                       # bkz. asagi: tum klasorlerdeki dosyali kayitlar
    # TUM klasorler eslestirmeye dahil: aktif, _ListeDisi ve diger _YayındanKalkan kayitlari (dosyasi varsa)
    for r in conn.execute("SELECT v.video_id, v.playlist_id, v.title, v.duration, v.file_path, p.alias, v.status "
                          "FROM videos v JOIN playlists p ON p.id=v.playlist_id "
                          "WHERE v.file_path IS NOT NULL").fetchall():
        p = resolve_video_path(r[4])
        if p and p.exists():
            adaylar.append({"video_id": r[0], "playlist_id": r[1], "title": r[2], "duration": r[3],
                            "alias": r[5], "dosya": str(p), "status": r[6],
                            "klasor": _ES_KLASOR_ETIKETI.get(r[6], r[6])})
    yt = youtube_durum_kayitlari(conn)
    for r in kalkan:
        r["yt_durum"], r["yt_kontrol"] = yt.get(r["video_id"], (None, None))      # 'yayinda' | 'kalkmis' | None (hic yoklanmadi)
        p = resolve_video_path(r["file_path"]) if r["file_path"] else None
        r["boyut"] = p.stat().st_size if p and p.exists() else 0
        r["es"] = _es_bul(r, adaylar) if r["boyut"] else None
    return kalkan


def _yayindan_kalkan_satirlari(conn):
    cur = conn.execute(
        "SELECT v.video_id, v.playlist_id, v.title, v.uploader, v.duration, v.file_path, v.first_seen, "
        "v.last_seen, v.playlist_index, p.alias AS playlist_alias, p.title AS playlist_title, "
        "(SELECT MIN(e.timestamp) FROM events e WHERE e.video_id=v.video_id AND e.playlist_id=v.playlist_id "
        " AND e.event_type='became_unavailable') AS kalkis "
        "FROM videos v JOIN playlists p ON p.id=v.playlist_id WHERE v.status='unavailable' "
        "ORDER BY p.alias, v.playlist_index, v.video_id")
    kolonlar = [d[0] for d in cur.description]
    return [dict(zip(kolonlar, tuple(r))) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# KONTROLOR: "Hepsini tara" tek kapisi.  GUI ve CLI taramayi YALNIZ buradan baslatir
# (cekirdek sync_playlists'i dogrudan cagirmak yasak - bkz. test_kapi.py).
#
# Sira:  1) YEREL isler (manuel klasorler, dosyalari yeniden bagla, klasor uzlastir)
#        2) TARAMA  (playlist metadata + diskten baglama; indirmeler ERTELENIR)
#        3) INDIRME (yalniz ertelenen videolari olan playlistler, tarama bittikten sonra)
# Cekirdek sync_playlists taramayi ve indirmeyi playlist basina ic ice yaptigindan,
# 2. asamada cekirdegin download_video'su gecici olarak "kaydet, indirme" ile degistirilir
# (yalniz bu is parcacigi icin) ve bu erteleme yuzunden olusacak 'download_failed'
# olayi / hata sayaci / "indiriliyor" log satiri bastirilir.
# ---------------------------------------------------------------------------
import threading as _threading


class _IndirmeErteleme:
    """Cekirdegin indirme/olay/metadata kapilarini bu is parcacigi icin gecici sarar."""

    def __init__(self, conn):
        self.conn = conn
        self.owner = _threading.get_ident()
        self.pending = []            # [(playlist_id, video_url)]
        self._cur_pid = None
        self._orig = {}

    def __enter__(self):
        m = _cached
        self._orig = {k: getattr(m, k) for k in ("download_video", "log_event", "fetch_playlist_meta")}
        orig = self._orig

        def download_video(video_url, dest_dir, playlist_index=None):
            if _threading.get_ident() != self.owner:
                return orig["download_video"](video_url, dest_dir, playlist_index)
            self.pending.append((self._cur_pid, video_url))
            return None

        def log_event(conn, event_type, *a, **k):
            if _threading.get_ident() == self.owner and event_type == "download_failed":
                return None          # erteleme hatasi degil, gercek hata sayilmaz
            return orig["log_event"](conn, event_type, *a, **k)

        def fetch_playlist_meta(url, *a, **k):
            if _threading.get_ident() == self.owner:
                r = self.conn.execute("SELECT id FROM playlists WHERE url=?", (url,)).fetchone()
                self._cur_pid = r[0] if r else None
            return orig["fetch_playlist_meta"](url, *a, **k)

        m.download_video, m.log_event, m.fetch_playlist_meta = download_video, log_event, fetch_playlist_meta
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(_cached, k, v)
        return False


def _erteleme_logu(log):
    def f(msg):
        s = str(msg)
        if "HATA: indirilemedi" in s:
            return
        log(s.replace(" indiriliyor: ", " indirme ertelendi: "))
    return f


# Derlenmis cekirdegin gunluk metinlerindeki eski/karisik kavramlari yeni adlara cevirir.
_KAVRAM_METIN = (("_silinenler/", LISTE_DISI_DIR + "/"),
                 ("PLAYLISTTEN SILINMIS", "LISTEDEN CIKARILMIS (senin eylemin)"))


def _kavram_logu(log):
    def f(msg):
        s = str(msg)
        for eski, yeni in _KAVRAM_METIN:
            s = s.replace(eski, yeni)
        log(s)
    return f


def sync_two_phase(conn, playlist_id=None, log=print, should_cancel=None, on_phase=None):
    """Yerel isler -> tarama (indirmesiz) -> indirme.  sync_playlists ile ayni ozet sozlugunu doner
    (+ 'deferred': ertelenen/indirilen sayisi, 'local': yerel asama ozeti)."""
    should_cancel = should_cancel or (lambda: False)
    on_phase = on_phase or (lambda t: None)
    log = _kavram_logu(log)

    # --- 1) YEREL ---
    on_phase("Yerel klasorler taraniyor / dosyalar baglaniyor...")
    log("\n=== 1/3 YEREL ISLER (manuel klasorler, dosya baglama, klasor uzlastirma) ===")
    if not DOWNLOADS.exists():
        log(f"  [!] downloads klasoru bulunamadi: {DOWNLOADS}")
    try:                                   # eski _silinenler/ klasorlerini _ListeDisi/'ya gecir (tekrar calisabilir)
        liste_disi_klasorlerini_tasi(conn, log=log)
    except Exception as e:
        log(f"  [!] klasor gecisi hatasi: {e}")
    local = {}
    for name, fn in (("manuel", sync_manual_collections),
                     ("baglama", scan_and_relink_files),
                     ("klasor", reconcile_download_folders)):
        if should_cancel():
            break
        try:
            local[name] = fn(conn, log=log)
        except Exception as e:
            log(f"  [!] yerel is hatasi ({name}): {e}")
    b = local.get("baglama") or {}
    log(f"  Yerel ozet: taranan dosya {b.get('scanned_files', 0)}, yeniden baglanan {b.get('relinked', 0)}, "
        f"hala kayip {b.get('no_match', 0)}")
    if should_cancel():
        log("[iptal edildi]")
        return {"new": 0, "removed": 0, "unavailable": 0, "errors": 0, "playlists": 0,
                "linked_existing": 0, "redownload_small": 0, "deferred": 0, "local": local}

    # --- 2) TARAMA (indirme yok) ---
    on_phase("Playlistler taraniyor (indirme sonra)...")
    log("\n=== 2/3 TARAMA (indirmeler en sona ertelendi) ===")
    with _IndirmeErteleme(conn) as defer:
        summary = _cached.sync_playlists(conn, playlist_id=playlist_id,
                                         log=_erteleme_logu(log), should_cancel=should_cancel)
    n_def = len(defer.pending)
    summary["errors"] = max(0, summary.get("errors", 0) - n_def)   # erteleme "hata" degil
    summary["deferred"] = n_def
    summary["local"] = local

    # --- 2b) ARSIV: yayindan kalkan + listeden cikan videolarin dosyalari downloads\_YayındanKalkan / _ListeDisi altina ---
    if not should_cancel():
        try:
            summary["kopya_cikarma_islenen"] = guvenli_azalmayi_isle(conn, log=log)
            summary["silinme_onay_bekleyen"] = len(silinme_bekleyenler(conn))
            arsiv = arsive_toparla(conn, log=log)
            summary["yayindan_kalkan_tasinan"] = arsiv["unavailable"]
            summary["liste_disi_tasinan"] = arsiv["removed_from_playlist"]
            summary["fazla_ad_silinen"] = fazla_arsiv_adlarini_temizle(conn, log=log)
        except Exception as e:
            log(f"  [!] arsive toplama hatasi: {e}")

    # --- 2c) LISTE DISI videolar YouTube'da hala var mi? (yalniz bilgi; durum/dosya degismez) ---
    if not should_cancel():
        try:
            summary["liste_disi_yoklanan"] = liste_disi_youtube_durumunu_yenile(conn, log=log)
        except Exception as e:
            log(f"  [!] liste disi YouTube yoklamasi hatasi: {e}")

    # --- 3) INDIRME ---
    if should_cancel():
        log("[iptal edildi - indirme baslatilmadi]")
        return summary
    if not n_def:
        log("\n=== 3/3 INDIRME: indirilecek video yok ===")
        return summary
    pids = []
    for pid, _url in defer.pending:
        if pid is not None and pid not in pids:
            pids.append(pid)
    log(f"\n=== 3/3 INDIRME: {n_def} video, {len(pids)} playlist ===")
    for pid in pids:
        if should_cancel():
            log("[iptal edildi]")
            break
        on_phase("Indiriliyor...")
        s3 = _cached.sync_playlists(conn, playlist_id=pid, log=log, should_cancel=should_cancel)
        summary["new"] = summary.get("new", 0) + s3.get("new", 0)
        summary["errors"] = summary.get("errors", 0) + s3.get("errors", 0)
    return summary

# ---------------------------------------------------------------------------
# SES PARMAK IZI (Chromaprint / fpcalc): "Ayni Videolar" gruplarindaki dosyalar gercekten AYNI KAYIT mi?
#
# Kapilar (baska kod fpcalc'i ya da ses_* tablolarini dogrudan kullanamaz - bkz. test_kapi.py):
#   _fpcalc_iz        TEK dis surec kapisi (sure siniri, konsol penceresi yok)
#   _iz_benzerligi    TEK karsilastirma kapisi
#   _ses_iz_yaz / _ses_cift_yaz   tablolara TEK yazma kapisi (commit'i ses_analizi verir)
#   ses_analizi       KONTROLOR: gruplari gez, onbellekte olmayani hesapla, yaz
#   ses_etiketleri    YALNIZ OKUR (GUI buradan etiket alir)
# Sonuc yalniz BILGIDIR: dosya silme/tasima yapmaz, status degistirmez.
# ---------------------------------------------------------------------------
import array as _array
import statistics as _statistics
import subprocess as _subprocess

SES_ARACI = ARAC_DIZINI / "fpcalc.exe"
SES_IZ_SANIYE = 120               # her dosyanin ilk 120 sn'si okunur
SES_BIRIM = 0.1238                # bir iz ogesi ~0.124 sn
SES_KAYMA_SN = 30                 # iki kayit arasi intro/bas farki icin aranan en buyuk kayma
SES_AYNI_ESIK = 0.94              # >= : ayni kayit (0.90-0.94 arasi belirsiz bolge: 'benzer' kalir, elle dinlenir)
SES_BENZER_ESIK = 0.68            # >= : ayni videonun farkli surumu (yalniz bilgi, silme onerilmez); alakasiz ciftler <=0.64
SES_SURE_TOLERANS = (SURUM_SURE_FARKI, 0.40)    # sn, oran: sureleri bundan uzak olanlar karsilastirilmaz


def _fpcalc_iz(yol, zaman_asimi=180):
    """(sure_sn, iz[int]) doner; arac yoksa / okunamazsa istisna firlatir."""
    if not SES_ARACI.is_file():
        raise FileNotFoundError(f"fpcalc bulunamadi: {SES_ARACI}")
    r = _subprocess.run(
        [str(SES_ARACI), "-raw", "-length", str(SES_IZ_SANIYE), str(yol)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=zaman_asimi,
        creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0))
    sure, iz = None, None
    for satir in r.stdout.splitlines():
        if satir.startswith("DURATION="):
            sure = int(float(satir[len("DURATION="):]))
        elif satir.startswith("FINGERPRINT="):
            iz = [int(x) & 0xFFFFFFFF for x in satir[len("FINGERPRINT="):].split(",") if x]
    if not iz:
        raise ValueError(f"iz cikarilamadi: {(r.stderr or '').strip()[:150]}")
    return sure, iz


def _iz_benzerligi(a, b):
    """(benzerlik, blok_medyani): a'nin ortasindan alinan ~37 sn'lik parcayi b icinde +-SES_KAYMA_SN kaydirarak
    en iyi eslesme (0..1); sonra o kaymayla 20 sn'lik bloklarin medyani.  Ayni kayit ~0.95+, rastgele ~0.5-0.6."""
    pencere = min(300, len(a) // 2, len(b) // 2)
    if pencere < 60:
        return 0.0, 0.0
    maks = int(SES_KAYMA_SN / SES_BIRIM)
    orta = len(a) // 2 - pencere // 2
    parca = a[orta:orta + pencere]
    en_iyi, en_k = 0.0, 0
    for k in range(-maks, maks + 1):
        j = orta + k
        if j < 0 or j + pencere > len(b):
            continue
        h = sum((x ^ y).bit_count() for x, y in zip(parca, b[j:j + pencere]))
        o = 1 - h / (32 * pencere)
        if o > en_iyi:
            en_iyi, en_k = o, k
    blok = int(20 / SES_BIRIM)
    oranlar = []
    for bas in range(0, len(a) - blok + 1, blok):
        j = bas + en_k
        if j < 0 or j + blok > len(b):
            continue
        h = sum((x ^ y).bit_count() for x, y in zip(a[bas:bas + blok], b[j:j + blok]))
        oranlar.append(1 - h / (32 * blok))
    return en_iyi, (_statistics.median(oranlar) if oranlar else 0.0)


def ses_etiketi(benzerlik, blok=1.0):
    """'ayni' | 'benzer' | 'farkli'.  benzerlik None = sureleri cok farkli (karsilastirilmadi) -> 'farkli'."""
    if benzerlik is None:
        return "farkli"
    if benzerlik >= SES_AYNI_ESIK and (blok or 0) >= 0.85:
        return "ayni"
    return "benzer" if benzerlik >= SES_BENZER_ESIK else "farkli"


_SES_AD = {"ayni": "AYNI", "benzer": "benzer", "farkli": "farkli"}


def ses_metni(e):
    """GUI sutunu icin kisa metin; e = ses_etiketleri() ogesi ya da None."""
    if not e:
        return "-"
    if e["benzerlik"] is None:
        return "sure farkli"
    return f"{_SES_AD[e['etiket']]} {e['benzerlik']:.2f}".replace(".", ",")


def _sure_yakin(a, b):
    if not a or not b:
        return True
    sn, oran = SES_SURE_TOLERANS
    return abs(a - b) <= max(sn, oran * max(a, b))


def _ses_anahtar(p):
    """Tabloda dosya anahtari: DOWNLOADS'a gore GORELI yol ('/' ayiracli) - klasor tasinsa da gecerli kalir."""
    p = Path(p)
    try:
        return p.resolve().relative_to(Path(DOWNLOADS).resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def _ses_kimlik(p, boyut=None):
    """Ses kayitlarinin KIMLIGI: 'video_id#boyut'.  Dosya yeniden numaralansa / _ListeDisi'ne tasinsa / sabit bagla
    ikinci ad alsa da DEGISMEZ; [id] icermeyen dosyada goreli yol + boyut kullanilir."""
    p = Path(p)
    boyut = p.stat().st_size if boyut is None else boyut
    m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", p.stem)
    return f"{m.group(1) if m else _ses_anahtar(p)}#{boyut}"


def _ses_tablolari(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS ses_iz ("
                 "kimlik TEXT PRIMARY KEY, boyut INTEGER, sure INTEGER, iz BLOB, hesaplandi TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS ses_eslesme ("
                 "kimlik_a TEXT, kimlik_b TEXT, boyut_a INTEGER, boyut_b INTEGER, benzerlik REAL, blok REAL, "
                 "hesaplandi TEXT, PRIMARY KEY (kimlik_a, kimlik_b))")


def _ses_iz_yaz(conn, anahtar, boyut, sure, iz):
    conn.execute("INSERT OR REPLACE INTO ses_iz (kimlik, boyut, sure, iz, hesaplandi) VALUES (?,?,?,?,?)",
                 (anahtar, boyut, sure, _array.array("I", iz).tobytes(), _datetime.now().isoformat(timespec="seconds")))


def _ses_cift_yaz(conn, a, b, boyut_a, boyut_b, benzerlik, blok):
    conn.execute("INSERT OR REPLACE INTO ses_eslesme (kimlik_a, kimlik_b, boyut_a, boyut_b, benzerlik, blok, hesaplandi) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (a, b, boyut_a, boyut_b, benzerlik, blok, _datetime.now().isoformat(timespec="seconds")))


def _ses_ham_yollar(dups):
    ham = []
    for kat in ("exact", "fuzzy"):
        for ogeler in (dups.get(kat) or {}).values():
            ham.append([resolve_video_path(o.get("file_path")) for o in ogeler if o.get("file_path")])
    for kat in ("disk_dupes", "disk_orphans"):
        for bilgi in (dups.get(kat) or {}).values():
            ham.append([Path(p) for p in bilgi.get("paths", [])])
    return ham


def _ses_gruplari(dups):
    """find_duplicates() sonucundan [(kimlik, Path, boyut), ...] gruplari (en az 2 FARKLI dosyali, dosyasi diskte olan).
    Ayni dosyanin sabit bagli adlari tek dugum sayilir."""
    gruplar = []
    for yollar in _ses_ham_yollar(dups):
        goruldu, uyeler = set(), []
        for p in yollar:
            if not p or not p.is_file():
                continue
            boyut = p.stat().st_size
            k = _ses_kimlik(p, boyut)
            if k in goruldu:
                continue
            goruldu.add(k)
            uyeler.append((k, p, boyut))
        if len(uyeler) >= 2:
            gruplar.append(uyeler)
    return gruplar


def _ses_cifti(a, b):
    return (a, b) if a[0] <= b[0] else (b, a)


def ses_analizi(conn, dups=None, log=print, should_cancel=None, include_inactive=True):
    """KONTROLOR (tablolara yazan TEK yer).  'Ayni Videolar' gruplarindaki dosya ciftlerini ses parmak iziyle
    karsilastirir; onbellekte (dosya boyutu ayni) olani yeniden hesaplamaz.  dups verilmezse HAM baslik gruplari
    kullanilir (ses ile bolunmus gruplar degil).  Doner: ozet sozlugu."""
    _ses_tablolari(conn)
    if dups is None:
        dups = _find_duplicates_baslik(conn, include_inactive=include_inactive, surum_adaylari=True)
    gruplar = _ses_gruplari(dups)
    ozet = {"grup": len(gruplar), "cift": 0, "yeni_cift": 0, "yeni_iz": 0, "hata": 0,
            "ayni": 0, "benzer": 0, "farkli": 0, "iptal": False}
    iz_onbellek = {}

    def iz_al(anahtar, p, boyut):
        if anahtar in iz_onbellek:
            return iz_onbellek[anahtar]
        satir = conn.execute("SELECT boyut, sure, iz FROM ses_iz WHERE kimlik=?", (anahtar,)).fetchone()
        if satir and satir[0] == boyut:
            arr = _array.array("I")
            arr.frombytes(satir[2])
            iz_onbellek[anahtar] = (satir[1], list(arr))
        else:
            sure, iz = _fpcalc_iz(p)
            _ses_iz_yaz(conn, anahtar, boyut, sure, iz)
            ozet["yeni_iz"] += 1
            iz_onbellek[anahtar] = (sure, iz)
        return iz_onbellek[anahtar]

    for gi, uyeler in enumerate(gruplar, 1):
        if should_cancel and should_cancel():
            ozet["iptal"] = True
            log("[ses] iptal edildi")
            break
        for i in range(len(uyeler)):
            for j in range(i + 1, len(uyeler)):
                a, b = _ses_cifti(uyeler[i], uyeler[j])
                ozet["cift"] += 1
                onb = conn.execute("SELECT boyut_a, boyut_b, benzerlik, blok FROM ses_eslesme WHERE kimlik_a=? AND kimlik_b=?",
                                   (a[0], b[0])).fetchone()
                if onb and onb[0] == a[2] and onb[1] == b[2]:
                    ozet[ses_etiketi(onb[2], onb[3])] += 1
                    continue
                try:
                    sa, ia = iz_al(*a)
                    sb, ib = iz_al(*b)
                except Exception as e:
                    ozet["hata"] += 1
                    log(f"[ses] iz cikarilamadi ({a[1].name[:50]} / {b[1].name[:50]}): {str(e)[:120]}")
                    continue
                benzerlik, blok = (None, None) if not _sure_yakin(sa, sb) else _iz_benzerligi(ia, ib)
                _ses_cift_yaz(conn, a[0], b[0], a[2], b[2], benzerlik, blok)
                ozet["yeni_cift"] += 1
                ozet[ses_etiketi(benzerlik, blok)] += 1
        conn.commit()
        if gi % 10 == 0 or gi == len(gruplar):
            log(f"[ses] {gi}/{len(gruplar)} grup islendi (yeni iz: {ozet['yeni_iz']})")
    conn.commit()
    return ozet


def ses_etiketleri(conn, dups):
    """YALNIZ OKUR.  {str(dosya_yolu): {'etiket','benzerlik','blok'}}: her dosya icin grubundaki en yuksek benzerlikli
    cift.  Analiz edilmemis dosya haritada yoktur (GUI '-' gosterir)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ses_eslesme'").fetchone():
        return {}
    kimlikli = {}
    for uyeler in _ses_gruplari(dups):
        for i in range(len(uyeler)):
            for j in range(i + 1, len(uyeler)):
                a, b = _ses_cifti(uyeler[i], uyeler[j])
                r = conn.execute("SELECT boyut_a, boyut_b, benzerlik, blok FROM ses_eslesme WHERE kimlik_a=? AND kimlik_b=?",
                                 (a[0], b[0])).fetchone()
                if not r or r[0] != a[2] or r[1] != b[2]:
                    continue
                e = {"etiket": ses_etiketi(r[2], r[3]), "benzerlik": r[2], "blok": r[3]}
                for k in (a[0], b[0]):
                    eski = kimlikli.get(k)
                    if eski is None or (e["benzerlik"] or -1) > (eski["benzerlik"] or -1):
                        kimlikli[k] = e
    sonuc = {}                                   # her AD (sabit bagli adlar dahil) kendi dosyasinin etiketini alir
    for yollar in _ses_ham_yollar(dups):
        for p in yollar:
            if p and p.is_file():
                e = kimlikli.get(_ses_kimlik(p))
                if e:
                    sonuc[str(p)] = e
    return sonuc


# ---------------------------------------------------------------------------
# "AYNI VIDEOLAR" TEK KAPISI (GUI/CLI yalniz bunu cagirir): baslik gruplari + ses eslesmesine gore bolme.
# Baslik benzerligi sanatci adi yuzunden farkli videolari zincirleme tek gruba toplayabilir (ornek: 19 farkli
# SAVAGE-44 parcasi tek grup oldu).  TUM dosya ciftleri ses analizinden gecmis bir grup, ses eslesmelerine
# ('ayni' / 'benzer') gore bolunur; hicbiriyle eslesmeyen uye listeden duser.  Analizi eksik grup oldugu gibi kalir.
# ---------------------------------------------------------------------------
def _ses_bolumle(conn, uyeler):
    """uyeler: [(anahtar, boyut, oge), ...].  Tum ciftler analiz edilmisse ses-bagli bilesenleri
    [[oge, ...], ...] (en az 2 DOSYALI, ilk gorulme sirasinda) doner; eksik/eskimis cift varsa None."""
    dugum = {}
    for sira, (k, boyut, oge) in enumerate(uyeler):
        dugum.setdefault(k, (boyut, []))[1].append((sira, oge))
    anahtarlar = sorted(dugum)
    ata = {k: k for k in anahtarlar}

    def kok(x):
        while ata[x] != x:
            ata[x] = ata[ata[x]]
            x = ata[x]
        return x

    for i, ka in enumerate(anahtarlar):
        for kb in anahtarlar[i + 1:]:
            r = conn.execute("SELECT boyut_a, boyut_b, benzerlik, blok FROM ses_eslesme WHERE kimlik_a=? AND kimlik_b=?",
                             (ka, kb)).fetchone()
            if not r or r[0] != dugum[ka][0] or r[1] != dugum[kb][0]:
                return None
            if ses_etiketi(r[2], r[3]) != "farkli":
                ata[kok(ka)] = kok(kb)
    kumeler = {}
    for k in anahtarlar:
        kumeler.setdefault(kok(k), []).append(k)
    parcalar = []
    for anahtarlar_k in kumeler.values():
        if len(anahtarlar_k) >= 2:
            ogeler = sorted((so for k in anahtarlar_k for so in dugum[k][1]), key=lambda x: x[0])
            parcalar.append((ogeler[0][0], [o for _, o in ogeler]))
    return [p for _, p in sorted(parcalar, key=lambda x: x[0])]


def _ses_ile_ayir(conn, result):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ses_eslesme'").fetchone():
        return
    fuzzy = result.get("fuzzy") or {}
    yeni = {}
    for anahtar, ogeler in fuzzy.items():
        uyeler = []
        for o in ogeler:
            p = resolve_video_path(o.get("file_path")) if o.get("file_path") else None
            if not p or not p.is_file():
                uyeler = None
                break
            boyut = p.stat().st_size
            uyeler.append((_ses_kimlik(p, boyut), boyut, o))
        parcalar = _ses_bolumle(conn, uyeler) if uyeler else None
        if parcalar is None:
            yeni[anahtar] = ogeler
            continue
        for parca in parcalar:
            ad = (parca[0].get("title") or anahtar)[:60]
            while ad in yeni:
                ad += "'"
            yeni[ad] = parca
    fuzzy.clear()
    fuzzy.update(yeni)




# ---------------------------------------------------------------------------
# "MEVCUT DOSYA KULLANILDI" = KOPYA DEGIL, SABIT BAG (hard link).
# Cekirdegin materialize_existing_video'su baska playlist klasorundeki dosyayi shutil.copy2 ile KOPYALIYORDU:
# her "mevcut dosya kullanildi" satiri diskte dosya boyutu kadar yer tutuyordu (Klix/Music arasinda 7 GB bosa gitti,
# disk doldu).  Cekirdekte copy2'yi kullanan TEK yer burasidir; _cached.shutil bu vekille degistirilir:
#   1) ayni birimde sabit bag kurar (yer kaplamaz; bir ismi silmek oburunu etkilemez)
#   2) bag olmazsa kopyalar - ama bos yer (dosya + YEDEK_YER) yoksa BASTAN reddeder, yarim dosya birakmaz
# ---------------------------------------------------------------------------
import errno as _errno
import os as _os
import shutil as _shutil


class _ShutilBag:
    YEDEK_YER = 1 * 1024 ** 3     # kopyadan sonra en az 1 GB bos kalmali

    def __getattr__(self, ad):
        return getattr(_shutil, ad)

    def copy2(self, src, dst, *a, **k):
        if _os.path.isdir(dst):
            dst = _os.path.join(dst, _os.path.basename(src))
        try:
            _os.link(src, dst)
            return dst
        except OSError:
            pass                       # farkli birim / dosya var / bag desteklenmiyor -> kopya yolu
        boyut = _os.path.getsize(src)
        bos = _shutil.disk_usage(_os.path.dirname(_os.path.abspath(dst))).free
        if bos < boyut + self.YEDEK_YER:
            raise OSError(_errno.ENOSPC, f"yetersiz disk alani: {boyut / 2**20:.0f} MB kopyalanacak, "
                                         f"{bos / 2**20:.0f} MB bos (en az {self.YEDEK_YER / 2**20:.0f} MB kalmali)")
        return _shutil.copy2(src, dst, *a, **k)


_cached.shutil = _ShutilBag()


# ---------------------------------------------------------------------------
# DOSYA ESLESTIRME (taramanin EN SONUNDA, indirmelerden sonra): ayni video_id'nin birden cok dosyasi
#   - boyut + SHA-256 AYNI   -> sabit bag (hard link): yer kaplamaz, her klasor eksiksiz kalir
#   - boyut ya da ozet FARKLI -> kopya_farkli tablosuna KAYDEDILIR (dosyalara dokunulmaz, karar elle)
# Kapilar:  _ozet_sha256 (okuma)  |  _bagla (TEK baglama/degistirme kapisi, atomik os.replace)
#           _kopya_farkli_yaz (tabloya TEK yazma kapisi)  |  kopyalari_esle (KONTROLOR)  |  farkli_kopyalar (okuma)
# GUI/CLI bunlari dogrudan cagiramaz; sync_two_phase adimi kendisi calistirir (bkz. test_kapi.py).
# ---------------------------------------------------------------------------
import hashlib as _hashlib

_BAG_VID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]$")


def _ozet_sha256(yol):
    h = _hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(8 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def _bagla(kaynak, hedef):
    """hedef dosyayi kaynagin sabit bagi yapar (once gecici adla, sonra atomik os.replace: hedef hicbir anda yok olmaz).
    True = baglandi; False = kurulamadi (kilitli / farkli birim / bag desteklenmiyor) - hedefe dokunulmamistir."""
    gecici = hedef.with_name(hedef.name + ".baglanti.tmp")
    try:
        gecici.unlink(missing_ok=True)
        _os.link(kaynak, gecici)
    except OSError:
        return False
    try:
        _os.replace(gecici, hedef)
    except OSError:
        gecici.unlink(missing_ok=True)
        return False
    return True


def _kopya_tablosu(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS kopya_farkli ("
                 "video_id TEXT, yol_a TEXT, yol_b TEXT, boyut_a INTEGER, boyut_b INTEGER, neden TEXT, tespit TEXT, "
                 "PRIMARY KEY (yol_a, yol_b))")


def _kopya_farkli_yaz(conn, satirlar):
    """Tablo diskten TURETILIR: her kosuda guncel durumla degistirilir.  satirlar: [(video_id, a, b, ba, bb, neden, tespit)]"""
    conn.execute("DELETE FROM kopya_farkli")
    conn.executemany("INSERT INTO kopya_farkli (video_id, yol_a, yol_b, boyut_a, boyut_b, neden, tespit) "
                     "VALUES (?,?,?,?,?,?,?)", satirlar)


def kopyalari_esle(conn, log=print, should_cancel=None):
    """KONTROLOR.  Doner: {'video','baglandi','kazanilan_mb','farkli','yeni_farkli','atlandi','iptal'}."""
    should_cancel = should_cancel or (lambda: False)
    ozet = {"video": 0, "baglandi": 0, "kazanilan_mb": 0.0, "farkli": 0, "yeni_farkli": 0, "atlandi": 0, "iptal": False}
    if not Path(DOWNLOADS).is_dir():
        return ozet                                  # medya klasoru yoksa hicbir sey yapma
    _kopya_tablosu(conn)
    gruplar = {}
    for p in Path(DOWNLOADS).rglob("*"):
        if p.name.endswith(".baglanti.tmp"):         # yarim kalmis onceki baglama: yalniz bizim gecici adimiz
            p.unlink(missing_ok=True)
            continue
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        m = _BAG_VID_RE.search(p.stem)                # .fNNN / .temp parcalari eslesmez
        if m:
            gruplar.setdefault(m.group(1), []).append(p)

    simdi = _datetime.now().isoformat(timespec="seconds")
    eski = {(a, b): t for a, b, t in conn.execute("SELECT yol_a, yol_b, tespit FROM kopya_farkli")}
    farkli = {}
    for vid, dosyalar in gruplar.items():
        if len(dosyalar) < 2:
            continue
        if should_cancel():
            ozet["iptal"] = True
            break
        ozet["video"] += 1
        boyutlar = {}
        for p in dosyalar:
            boyutlar.setdefault(p.stat().st_size, []).append(p)
        temsil = {s: sorted(u, key=lambda x: str(x).lower())[0] for s, u in boyutlar.items()}
        sirali = sorted(boyutlar)
        for i, sa in enumerate(sirali):
            for sb in sirali[i + 1:]:
                a, b = _ses_anahtar(temsil[sa]), _ses_anahtar(temsil[sb])
                farkli[(a, b)] = (vid, sa, sb, "boyut")
        for s, uyeler in boyutlar.items():
            uyeler = sorted(uyeler, key=lambda x: str(x).lower())
            anahtar, h0 = uyeler[0], None
            for d in uyeler[1:]:
                if should_cancel():
                    ozet["iptal"] = True
                    break
                if d.stat().st_ino == anahtar.stat().st_ino:
                    continue                          # zaten ayni dosya
                try:
                    h0 = h0 or _ozet_sha256(anahtar)
                    esit = _ozet_sha256(d) == h0
                except OSError as e:
                    ozet["atlandi"] += 1
                    log(f"  [!] okunamadi, atlandi: {d.name[:60]} ({e})")
                    continue
                if not esit:
                    farkli[(_ses_anahtar(anahtar), _ses_anahtar(d))] = (vid, s, s, "ozet")
                elif _bagla(anahtar, d):
                    ozet["baglandi"] += 1
                    ozet["kazanilan_mb"] += s / 2 ** 20
                    log_event(conn, "dosya_baglandi", vid, None, f"{_ses_anahtar(d)} -> {_ses_anahtar(anahtar)}")
                else:
                    ozet["atlandi"] += 1
            if ozet["iptal"]:
                break
        if ozet["iptal"]:
            break
    if not ozet["iptal"]:                             # yarim tarama tabloyu bozmasin
        satirlar = []
        for (a, b), (vid, ba, bb, neden) in sorted(farkli.items()):
            tespit = eski.get((a, b)) or simdi
            satirlar.append((vid, a, b, ba, bb, neden, tespit))
            if (a, b) not in eski:
                ozet["yeni_farkli"] += 1
                log_event(conn, "kopya_farkli", vid, None, f"{neden}: {a} <> {b}")
        _kopya_farkli_yaz(conn, satirlar)
        ozet["farkli"] = len(satirlar)
    conn.commit()
    ozet["kazanilan_mb"] = round(ozet["kazanilan_mb"], 1)
    return ozet


def farkli_kopyalar(conn):
    """YALNIZ OKUR: ayni video_id'nin birbirinden FARKLI dosyalari (dict listesi)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kopya_farkli'").fetchone():
        return []
    cur = conn.execute("SELECT video_id, yol_a, yol_b, boyut_a, boyut_b, neden, tespit FROM kopya_farkli "
                       "ORDER BY video_id, yol_a")
    return [dict(zip(("video_id", "yol_a", "yol_b", "boyut_a", "boyut_b", "neden", "tespit"), tuple(r))) for r in cur]


_sync_two_phase_ham = sync_two_phase


def sync_two_phase(conn, playlist_id=None, log=print, should_cancel=None, on_phase=None):
    """Tarama kontrolorunun TEK kapisi: yerel isler -> tarama -> indirme -> 4) dosya eslestirme."""
    summary = _sync_two_phase_ham(conn, playlist_id=playlist_id, log=log, should_cancel=should_cancel, on_phase=on_phase)
    if should_cancel and should_cancel():
        return summary
    (on_phase or (lambda t: None))("Dosyalar eslestiriliyor...")
    log("\n=== 4/4 DOSYA ESLESTIRME (ayni dosyalar sabit bag, farkli olanlar kaydedilir) ===")
    try:
        e = kopyalari_esle(conn, log=log, should_cancel=should_cancel)
        summary["kopya_baglanan"] = e["baglandi"]
        summary["kopya_kazanilan_mb"] = e["kazanilan_mb"]
        summary["kopya_farkli"] = e["farkli"]
        log(f"  {e['video']} video birden cok dosyada: {e['baglandi']} sabit bagla birlestirildi "
            f"({e['kazanilan_mb']:.0f} MB bosaldi), {e['farkli']} farkli kopya kayitli"
            + (f", {e['atlandi']} atlandi" if e["atlandi"] else ""))
    except Exception as ex:
        log(f"  [!] dosya eslestirme hatasi: {ex}")
    return summary


# ---------------------------------------------------------------------------
# SES ANALIZI - KIMLIK GOCU + TUM KUTUPHANE
# Ses kayitlari artik DOSYA YOLUNA degil 'video_id#boyut' kimligine baglidir (yeniden numaralama / _ListeDisi'ne tasima /
# sabit bagli ikinci ad kaydi KAYBETTIRMEZ).  Eski yol anahtarli tablolar (ses_izi, ses_cift) ilk acilista yeni tablolara
# KOPYALANIR (eskiler silinmez).
# Tum kutuphane: her dosyanin izi cikarilir, ters indeksle (birebir ortak deger sayisi) ADAY ciftler bulunur, yalniz
# adaylar kesin karsilastirilir.  Yontem ayni kaydi (>=~0.85) bulur; 'benzer surum' (0.68-0.85) bu aramada yakalanmaz.
# ---------------------------------------------------------------------------
SES_ADAY_ESIK = 12        # ayni (+-1 kayma) konumda ortak parmak izi degeri sayisi (ayni kayit >=167, alakasiz <=1 olculdu)
SES_SIK_DEGER = 12        # bu kadar dosyadan FAZLASINDA gecen deger (sessizlik / ortak kalip) sayilmaz


def _ses_goc(conn):
    """Eski yol anahtarli ses tablolarini kimlik tablolarina kopyalar; yeni tablolar doluysa dokunmaz.  Doner: tasinan iz."""
    def var(ad):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (ad,)).fetchone()

    if not var("ses_izi"):
        return 0
    _ses_tablolari(conn)
    if conn.execute("SELECT COUNT(*) FROM ses_iz").fetchone()[0] or conn.execute("SELECT COUNT(*) FROM ses_eslesme").fetchone()[0]:
        return 0

    def kimlik(yol, boyut):
        m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", Path(yol).stem)
        return f"{m.group(1) if m else yol}#{boyut}"

    n = 0
    for yol, boyut, sure, blob in conn.execute("SELECT yol, boyut, sure, iz FROM ses_izi").fetchall():
        arr = _array.array("I")
        arr.frombytes(blob)
        _ses_iz_yaz(conn, kimlik(yol, boyut), boyut, sure, list(arr))
        n += 1
    if var("ses_cift"):
        for a, b, ba, bb, bs, blok in conn.execute(
                "SELECT yol_a, yol_b, boyut_a, boyut_b, benzerlik, blok FROM ses_cift").fetchall():
            (ka, ba), (kb, bb) = sorted(((kimlik(a, ba), ba), (kimlik(b, bb), bb)))
            if ka != kb:
                _ses_cift_yaz(conn, ka, kb, ba, bb, bs, blok)
    conn.commit()
    return n


_init_db_orig = init_db


def init_db():
    conn = _init_db_orig()
    try:
        _ses_goc(conn)
    except Exception:
        conn.rollback()           # goc basarisizsa acilisi bozma; eski tablolar yerinde, bir sonraki acilista tekrar denenir
    return conn


def _kutuphane_dosyalari():
    """[(kimlik, Path, boyut)]: [id] tasiyan, parca olmayan medya dosyalari; ayni dosyanin bagli adlari tek sayilir."""
    kok = Path(DOWNLOADS)
    sonuc, goruldu = [], set()
    if not kok.is_dir():
        return sonuc
    for p in sorted(kok.rglob("*"), key=lambda x: str(x).lower()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS or not _BAG_VID_RE.search(p.stem):
            continue
        boyut = p.stat().st_size
        k = _ses_kimlik(p, boyut)
        if k not in goruldu:
            goruldu.add(k)
            sonuc.append((k, p, boyut))
    return sonuc


def _aday_ciftler(izler):
    """izler: {kimlik: (sure, iz)} -> [(a, b)] a<b: birebir ortak deger sayisi >= SES_ADAY_ESIK (+-1 kayma), sureler yakin."""
    anahtarlar = sorted(izler)
    indeks = {}
    for i, k in enumerate(anahtarlar):
        for pos, v in enumerate(izler[k][1]):
            indeks.setdefault(v, []).append((i, pos))
    sayac = {}
    for liste in indeks.values():
        n = len(liste)
        if n < 2 or n > SES_SIK_DEGER:
            continue
        for x in range(n):
            i, pi = liste[x]
            for y in range(x + 1, n):
                j, pj = liste[y]
                if i == j:
                    continue
                anahtar = (i, j, pj - pi) if i < j else (j, i, pi - pj)
                sayac[anahtar] = sayac.get(anahtar, 0) + 1
    adaylar = set()
    for (i, j, kayma), n in sayac.items():
        if n + sayac.get((i, j, kayma - 1), 0) + sayac.get((i, j, kayma + 1), 0) >= SES_ADAY_ESIK \
                and _sure_yakin(izler[anahtarlar[i]][0], izler[anahtarlar[j]][0]):
            adaylar.add((anahtarlar[i], anahtarlar[j]))
    return sorted(adaylar)


def ses_kutuphane_analizi(conn, log=print, should_cancel=None, include_inactive=True):
    """KONTROLOR (tablolara yazan kapilardan biri).  TUM kutuphane: izleri cikar (onbellekli), aday ciftleri bul, kesin
    karsilastir, sonra baslik gruplarinin tum ciftlerini tamamla (ses_analizi).  Doner: ozet sozlugu."""
    should_cancel = should_cancel or (lambda: False)
    _ses_tablolari(conn)
    ozet = {"dosya": 0, "yeni_iz": 0, "hata": 0, "aday": 0, "yeni_cift": 0, "ayni": 0, "benzer": 0, "farkli": 0,
            "iptal": False, "baslik": None}
    dosyalar = _kutuphane_dosyalari()
    ozet["dosya"] = len(dosyalar)
    izler, eksik = {}, []
    for k, p, boyut in dosyalar:
        satir = conn.execute("SELECT boyut, sure, iz FROM ses_iz WHERE kimlik=?", (k,)).fetchone()
        if satir and satir[0] == boyut:
            arr = _array.array("I")
            arr.frombytes(satir[2])
            izler[k] = (satir[1], list(arr))
        else:
            eksik.append((k, p, boyut))
    log(f"[ses] kutuphane: {len(dosyalar)} dosya, izi hazir {len(izler)}, cikarilacak {len(eksik)}")
    for n, (k, p, boyut) in enumerate(eksik, 1):
        if should_cancel():
            ozet["iptal"] = True
            log("[ses] iptal edildi (cikarilan izler kayitli, kaldigi yerden devam eder)")
            conn.commit()
            return ozet
        try:
            sure, iz = _fpcalc_iz(p)
        except Exception as e:
            ozet["hata"] += 1
            log(f"[ses] iz cikarilamadi ({p.name[:60]}): {str(e)[:100]}")
            continue
        _ses_iz_yaz(conn, k, boyut, sure, iz)
        izler[k] = (sure, iz)
        ozet["yeni_iz"] += 1
        if n % 25 == 0:
            conn.commit()
            log(f"[ses] iz cikarma: {n}/{len(eksik)}")
    conn.commit()
    adaylar = _aday_ciftler(izler)
    ozet["aday"] = len(adaylar)
    log(f"[ses] {len(adaylar)} aday cift (ortak parmak izi degeri olan) kesin karsilastiriliyor")
    boyutlar = {k: b for k, _, b in dosyalar}
    for a, b in adaylar:
        if should_cancel():
            ozet["iptal"] = True
            break
        onb = conn.execute("SELECT 1 FROM ses_eslesme WHERE kimlik_a=? AND kimlik_b=?", (a, b)).fetchone()
        if onb:
            continue
        benzerlik, blok = _iz_benzerligi(izler[a][1], izler[b][1])
        _ses_cift_yaz(conn, a, b, boyutlar[a], boyutlar[b], benzerlik, blok)
        ozet["yeni_cift"] += 1
        ozet[ses_etiketi(benzerlik, blok)] += 1
    conn.commit()
    if not ozet["iptal"]:
        ozet["baslik"] = ses_analizi(conn, None, log=log, should_cancel=should_cancel, include_inactive=include_inactive)
        ozet["iptal"] = bool(ozet["baslik"] and ozet["baslik"].get("iptal"))
    return ozet


def _ses_kutuphane_gruplari(conn, result, include_inactive):
    """YALNIZ OKUR.  Ses analizinde (baslik farki fark etmeksizin) 'ayni'/'benzer' bulunan dosyalari, listede henuz birlikte
    olmayan gruplar halinde 'fuzzy'ye ekler; tamami zaten bir grubun icinde olan bilesenler atlanir, eksik kalan
    (daha dar) baslik gruplari yeni grupla degistirilir."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ses_eslesme'").fetchone():
        return
    kenarlar = [(a, b) for a, b, bs, blok in conn.execute(
        "SELECT kimlik_a, kimlik_b, benzerlik, blok FROM ses_eslesme WHERE benzerlik IS NOT NULL")
        if ses_etiketi(bs, blok) != "farkli"]
    if not kenarlar:
        return
    sorgu = ("SELECT v.video_id, v.playlist_id, v.title, v.uploader, v.duration, v.status, v.file_path, "
             "v.playlist_index, p.alias, p.title, p.url FROM videos v JOIN playlists p ON p.id=v.playlist_id "
             "WHERE v.file_path IS NOT NULL")
    if not include_inactive:
        sorgu += " AND v.status='active'"
    ogeler = {}
    for r in conn.execute(sorgu).fetchall():
        if r[0] in DUPLICATE_EXCEPTIONS:
            continue
        p = resolve_video_path(r[6])
        if p and p.is_file():
            ogeler.setdefault(_ses_kimlik(p), []).append(
                {"video_id": r[0], "playlist_id": r[1], "title": r[2], "uploader": r[3], "duration": r[4],
                 "status": r[5], "file_path": r[6], "playlist_index": r[7],
                 "playlist_alias": (r[8] or "") + {"removed_from_playlist": " [liste disi]",
                                                    "unavailable": " [yayindan kalkan]"}.get(r[5], ""),
                 "playlist_title": r[9], "playlist_url": r[10]})
    ata = {}

    def kok(x):
        while ata.get(x, x) != x:
            ata[x] = ata.get(ata[x], ata[x])
            x = ata[x]
        return x

    dugumler = set()
    for a, b in kenarlar:
        if a in ogeler and b in ogeler:
            ata[kok(a)] = kok(b)
            dugumler.update((a, b))
    kumeler = {}
    for d in sorted(dugumler):
        kumeler.setdefault(kok(d), []).append(d)
    fuzzy = result.setdefault("fuzzy", {})

    def kimlikler(ogeler_listesi):
        return {(o["video_id"], o["playlist_id"]) for o in ogeler_listesi}

    for dugum_listesi in kumeler.values():
        uyeler = [o for d in dugum_listesi for o in ogeler[d]]
        yeni_idler = kimlikler(uyeler)
        mevcutlar = [kimlikler(v) for v in list(result.get("exact", {}).values()) + list(fuzzy.values())]
        if len({o["video_id"] for o in uyeler}) < 2 or any(yeni_idler <= m for m in mevcutlar):
            continue                                  # ya ayni video (exact kapsar) ya da zaten birlikte listeleniyor
        for anahtar in [k for k, v in fuzzy.items() if kimlikler(v) <= yeni_idler]:
            del fuzzy[anahtar]                        # daha dar baslik grubunu bu ses grubu kapsiyor
        ad = "~ " + ((uyeler[0].get("title") or "ses eslesmesi")[:58])
        while ad in fuzzy:
            ad += "'"
        fuzzy[ad] = uyeler


def find_duplicates(conn, include_inactive=False):
    """'Ayni Videolar' TEK kapisi: baslik gruplari -> ses ile bolme -> kutuphane ses eslesmelerini ekleme."""
    result = _find_duplicates_baslik(conn, include_inactive=include_inactive)
    _ses_ile_ayir(conn, result)
    _ses_kutuphane_gruplari(conn, result, include_inactive)
    _tasinanlari_ayikla(conn, result)        # kutuphane ses gruplari da eklendi: tasinan (ayni dosyayi paylasan) satirlar yine cikarilir
    _ayni_dosya_satirlarini_tekille(result)
    return result
