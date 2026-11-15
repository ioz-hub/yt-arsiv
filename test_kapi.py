"""Bekci testi: "Hepsini tara" tek kapidan (ytarsiv_core.sync_two_phase) gecer.

Calistir:  py -3.14 -m unittest test_kapi -v
Gercek DB/indirme klasorune DOKUNMAZ: kodu gecici klasore kopyalar, ag/indirme sahtedir.
"""
import importlib
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class StatikKapi(unittest.TestCase):
    def test_gui_ve_cli_cekirdek_sync_playlisti_dogrudan_cagirmaz(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bsync_playlists\s*\(", src), f"{ad}: sync_playlists kapiyi atliyor")
            self.assertIn("sync_two_phase", src, f"{ad}: sync_two_phase kullanmiyor")


class Akis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ytarsiv_test_"))
        for f in ("ytarsiv_core.py", "ytarsiv_core_cached.pyc"):
            shutil.copy2(HERE / f, self.tmp / f)
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        sys.path.insert(0, str(self.tmp))
        self.core = importlib.import_module("ytarsiv_core")
        self.core._video_durumu = lambda vid: ("belirsiz", "test: ag yok")      # testler asla aga cikmaz
        c = self.core._cached
        c.DOWNLOADS = self.tmp / "downloads"
        c.DOWNLOADS.mkdir()
        self.core.DOWNLOADS = c.DOWNLOADS
        self.conn = self.core.init_db()
        self.calls = []
        self._fake()

    def tearDown(self):
        self.conn.close()
        sys.path.remove(str(self.tmp))
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake(self):
        c = self.core._cached
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(playlists)")]
        for i, alias in ((1, "ListeA"), (2, "ListeB")):
            vals = {"url": f"https://www.youtube.com/playlist?list=PL{i}", "alias": alias, "title": alias,
                    "added_at": "2026-01-01T00:00:00"}
            use = {k: v for k, v in vals.items() if k in cols}
            self.conn.execute(
                f"INSERT INTO playlists ({','.join(use)}) VALUES ({','.join('?' * len(use))})", tuple(use.values()))
        self.conn.commit()
        # yerelde yalniz AAAAAAAAAAA var (ListeA klasorunde)
        d = c.DOWNLOADS / "ListeA"
        d.mkdir()
        (d / "001 - Bir [AAAAAAAAAAA].mp4").write_bytes(b"x" * 2_000_000)

        entries = {
            "PL1": [("AAAAAAAAAAA", "Bir"), ("BBBBBBBBBBB", "Iki")],
            "PL2": [("CCCCCCCCCCC", "Uc"), ("DDDDDDDDDDD", "Dort")],
        }

        def meta(url, *a, **k):
            self.calls.append("meta")
            key = url.split("list=")[1]
            return {"title": key, "entries": [
                {"id": v, "title": t, "uploader": "u", "duration": 100,
                 "url": f"https://www.youtube.com/watch?v={v}", "playlist_index": i + 1}
                for i, (v, t) in enumerate(entries[key])]}

        def dl(video_url, dest_dir, playlist_index=None):
            self.calls.append("download")
            vid = video_url.split("v=")[1]
            dest_dir = Path(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            p = dest_dir / f"{playlist_index or 0:03d} - T [{vid}].mp4"
            p.write_bytes(b"x" * 2_000_000)
            return str(p)

        c.fetch_playlist_meta, c.download_video = meta, dl
        # yerel asama cagrilarini izle
        for ad in ("sync_manual_collections", "scan_and_relink_files", "reconcile_download_folders"):
            orig = getattr(c, ad)

            def sar(conn, log=print, _o=orig, _a=ad):
                self.calls.append("yerel:" + _a)
                return _o(conn, log=log)
            setattr(c, ad, sar)
            setattr(self.core, ad, sar)

    def test_siralama_yerel_sonra_tarama_sonra_indirme(self):
        s = self.core.sync_two_phase(self.conn, log=lambda m: None)
        yerel = [i for i, x in enumerate(self.calls) if x.startswith("yerel:")]
        metas = [i for i, x in enumerate(self.calls) if x == "meta"]
        dls = [i for i, x in enumerate(self.calls) if x == "download"]
        self.assertEqual(len(yerel), 3)
        self.assertGreaterEqual(len(metas), 2)
        self.assertEqual(len(dls), 3)                       # B, C, D
        self.assertLess(max(yerel), min(metas))             # once yerel isler
        self.assertLess(min(dls), 10 ** 9)
        # tum playlistlerin ilk taramasi (2 meta) ilk indirmeden ONCE bitmis olmali
        self.assertLess(metas[1], dls[0])
        self.assertEqual(s["new"], 3)
        self.assertEqual(s["errors"], 0)
        self.assertEqual(s["linked_existing"], 1)
        self.assertEqual(s["deferred"], 3)
        n_fail = self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='download_failed'").fetchone()[0]
        self.assertEqual(n_fail, 0)
        nofile = self.conn.execute(
            "SELECT COUNT(*) FROM videos WHERE status='active' AND file_path IS NULL").fetchone()[0]
        self.assertEqual(nofile, 0)

    def test_iptal_edilirse_indirme_baslamaz(self):
        sayac = {"n": 0}

        def iptal():
            sayac["n"] += 1
            return any(x == "meta" for x in self.calls) and True

        self.core.sync_two_phase(self.conn, log=lambda m: None, should_cancel=iptal)
        self.assertNotIn("download", self.calls)

    def test_gercek_cekirdek_geri_yuklenir(self):
        c = self.core._cached
        once = (c.download_video, c.log_event, c.fetch_playlist_meta)
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        self.assertEqual(once, (c.download_video, c.log_event, c.fetch_playlist_meta))


class Kapilar(unittest.TestCase):
    """Cekirdek kabugundaki uc kapi: _inceleme kapali, parca dosyalar video degil, tekil kopya."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ytarsiv_kapi_"))
        for f in ("ytarsiv_core.py", "ytarsiv_core_cached.pyc"):
            shutil.copy2(HERE / f, self.tmp / f)
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        sys.path.insert(0, str(self.tmp))
        self.core = importlib.import_module("ytarsiv_core")
        self.core._video_durumu = lambda vid: ("belirsiz", "test: ag yok")      # testler asla aga cikmaz
        c = self.core._cached
        c.DOWNLOADS = self.tmp / "downloads"
        c.DOWNLOADS.mkdir()
        self.core.DOWNLOADS = c.DOWNLOADS
        # GERCEK DB'ye asla dokunma: DB yolu gecici klasorde olmali
        self.assertTrue(str(c.DB_PATH).startswith(str(self.tmp)), c.DB_PATH)
        self.conn = self.core.init_db()
        self.indirme = []
        self.entries = {}
        for i, alias in ((1, "Klix"), (2, "Euro3")):
            self.conn.execute("INSERT INTO playlists (url, alias, title, added_at) VALUES (?,?,?,?)",
                              (f"https://www.youtube.com/playlist?list=PL{i}", alias, alias, "2026-01-01T00:00:00"))
        self.conn.commit()

        def meta(url, *a, **k):
            key = url.split("list=")[1]
            return {"title": key, "entries": [
                {"id": v, "title": t, "uploader": "u", "duration": 100,
                 "url": f"https://www.youtube.com/watch?v={v}", "playlist_index": i + 1}
                for i, (v, t) in enumerate(self.entries[key])]}

        def dl(video_url, dest_dir, playlist_index=None):
            self.indirme.append(video_url)
            raise AssertionError("bu testte indirme olmamali")

        c.fetch_playlist_meta, c.download_video = meta, dl
        # Kabuktaki fetch kapisini sahte meta'nin USTUNE yeniden sar (kapi gercek kodla calissin)
        self._ham_meta = meta

    def tearDown(self):
        self.conn.close()
        sys.path.remove(str(self.tmp))
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- yardimcilar ---
    def pid(self, alias):
        return self.conn.execute("SELECT id FROM playlists WHERE alias=?", (alias,)).fetchone()[0]

    def dosya(self, klasor, ad, boyut=2_000_000):
        d = self.core.DOWNLOADS / klasor
        d.mkdir(parents=True, exist_ok=True)
        (d / ad).write_bytes(b"x" * boyut)
        return f"{klasor}\\{ad}"

    def satir(self, vid, alias, status="active", file_path=None):
        self.conn.execute(
            "INSERT INTO videos (video_id, playlist_id, title, status, file_path, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?)", (vid, self.pid(alias), "T", status, file_path, "2026-01-01", "2026-01-01"))
        self.conn.commit()

    def kapiyi_tak(self):
        """Test sahte meta'sini kabuktaki gercek fetch kapisinin icine yerlestirir."""
        c = self.core._cached
        orig = self._ham_meta
        gercek_kapi = self.core.fetch_playlist_meta     # kabuktaki kapi (uzak cagriyi _orig'e yapar)
        gercek_kapi.__globals__["_fetch_playlist_meta_orig"] = orig
        c.fetch_playlist_meta = gercek_kapi

    # --- KAPI 1: _inceleme ---
    def _kucuk_dosyali_liste(self):
        self.entries["PL1"] = [(f"AAAAAAAAAA{i}", f"S{i}") for i in range(6)]
        for i in range(6):
            boyut = 10_000 if i == 5 else 2_000_000      # 6. dosya cok kucuk
            fp = self.dosya("Klix", f"{i + 1:03d} - S{i} [AAAAAAAAAA{i}].mp4", boyut)
            self.satir(f"AAAAAAAAAA{i}", "Klix", file_path=fp)
        self.kapiyi_tak()

    def _kucuk_dosya_yolu(self):
        fp = self.conn.execute("SELECT file_path FROM videos WHERE video_id='AAAAAAAAAA5'").fetchone()[0]
        return self.core.resolve_video_path(fp)

    def test_kucuk_dosya_inceleme_ye_tasinmaz(self):
        self._kucuk_dosyali_liste()
        s = self.core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=lambda m: None)
        p = self._kucuk_dosya_yolu()
        self.assertTrue(p and p.exists())
        self.assertNotIn("_inceleme", p.parts)
        self.assertEqual(s.get("flagged_small", 0), 0)
        n = self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='moved_to_inceleme'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_kontrol_kapi_acilirsa_kucuk_dosya_tasinir(self):
        # testin gercekten bir sey yakaladigini kanitlar (kapi kapaliyken tasinmamasi anlamli olsun)
        self.core.INCELEME_AKTIF = True
        self._kucuk_dosyali_liste()
        self.core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=lambda m: None)
        self.assertIn("_inceleme", self._kucuk_dosya_yolu().parts)

    # --- KAPI 2: parca dosyalar ---
    def test_parca_dosyalar_video_sayilmaz(self):
        for ad in ("043 - Sarki [xtCKKbjRlPA].f251.webm", "043 - Sarki [xtCKKbjRlPA].f401.mp4",
                   "043 - Sarki [xtCKKbjRlPA].temp.mp4"):
            self.assertIsNone(self.core.extract_video_id(Path(ad).stem), ad)
            self.dosya("Klix", ad)
        self.assertNotIn("xtCKKbjRlPA", self.core._cached._build_disk_index())
        self.assertEqual(self.core.find_duplicates(self.conn)["disk_dupes"], {})

    def test_gercek_iki_kopya_hala_bulunur(self):
        self.dosya("Klix", "001 - Sarki [xtCKKbjRlPA].mp4")
        self.dosya("Euro3", "004 - Sarki [xtCKKbjRlPA].mp4")
        self.assertEqual(self.core.extract_video_id("001 - Sarki [xtCKKbjRlPA]"), "xtCKKbjRlPA")
        self.assertIn("xtCKKbjRlPA", self.core.find_duplicates(self.conn)["disk_dupes"])

    def _bagli_ad(self, kaynak_yol, klasor, ad):
        import os
        d = self.core.DOWNLOADS / klasor
        d.mkdir(parents=True, exist_ok=True)
        os.link(kaynak_yol, d / ad)
        return d / ad

    def test_ayni_dosyanin_bagli_adlari_tekrar_sayilmaz(self):
        self.dosya("Klix", "001 - Sarki [xtCKKbjRlPA].mp4")
        kaynak = self.core.DOWNLOADS / "Klix" / "001 - Sarki [xtCKKbjRlPA].mp4"
        self._bagli_ad(kaynak, "Music", "005 - Sarki [xtCKKbjRlPA].mp4")
        self._bagli_ad(kaynak, "Klix\\_ListeDisi", "001 - Sarki [xtCKKbjRlPA].mp4")
        self.assertNotIn("xtCKKbjRlPA", self.core.find_duplicates(self.conn, include_inactive=True)["disk_dupes"],
                         "tek fiziksel dosya tekrar degildir (silmek yer acmaz)")

    def test_bagli_adlar_arasinda_gercek_kopya_bir_kez_listelenir_ve_arsiv_adi_temsil_etmez(self):
        self.dosya("Klix", "001 - Sarki [xtCKKbjRlPA].mp4")
        kaynak = self.core.DOWNLOADS / "Klix" / "001 - Sarki [xtCKKbjRlPA].mp4"
        self._bagli_ad(kaynak, "Music", "005 - Sarki [xtCKKbjRlPA].mp4")
        self._bagli_ad(kaynak, "Klix\\_ListeDisi", "001 - Sarki [xtCKKbjRlPA].mp4")
        self.dosya("Euro3", "004 - Sarki [xtCKKbjRlPA].mp4", boyut=1_900_000)          # gercekten ayri dosya
        girdi = self.core.find_duplicates(self.conn, include_inactive=True)["disk_dupes"]["xtCKKbjRlPA"]
        self.assertEqual(len(girdi["paths"]), 2, "iki fiziksel dosya = iki girdi")
        self.assertEqual(len(girdi["relative_paths"]), 2)
        klasorler = {r.split("\\")[0] for r in girdi["relative_paths"]}
        self.assertIn("Euro3", klasorler)
        self.assertFalse(any("_ListeDisi" in r for r in girdi["relative_paths"]), "arsiv adi temsilci olmamali")

    def test_db_disi_ad_bilinen_dosyanin_fazladan_bagiysa_listelenmez(self):
        fp = self.dosya("Klix", "001 - Sarki [xtCKKbjRlPA].mp4")
        self.satir("xtCKKbjRlPA", "Klix", file_path=fp)
        kaynak = self.core.DOWNLOADS / "Klix" / "001 - Sarki [xtCKKbjRlPA].mp4"
        self._bagli_ad(kaynak, "Music", "005 - Sarki [xtCKKbjRlPA].mp4")           # DB'de yok ama ayni dosya
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertNotIn("xtCKKbjRlPA", d["disk_orphans"], "fazladan bag adi silmeyi gerektirmez")
        self.assertNotIn("xtCKKbjRlPA", d["disk_dupes"])

    def test_gercekten_db_disi_dosya_ve_ayri_kopya_hala_listelenir(self):
        self.dosya("Klix", "005 - Yetim [yYyYyYyYyY1].mp4")                           # DB'de hic yok
        fp = self.dosya("Klix", "001 - Sarki [xtCKKbjRlPA].mp4")
        self.satir("xtCKKbjRlPA", "Klix", file_path=fp)
        self.dosya("Euro3", "005 - Sarki [xtCKKbjRlPA].mp4", boyut=1_900_000)         # AYRI icerik, DB'de yok
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertIn("yYyYyYyYyY1", d["disk_orphans"], "gercek yetim dosya listelenmeli")
        self.assertIn("xtCKKbjRlPA", d["disk_orphans"], "ayri fiziksel kopya listelenmeli (yer kaplar)")

    def test_arsivdeki_ad_baska_ad_yoksa_temsil_eder(self):
        self.dosya("Klix\\_ListeDisi", "001 - Sarki [xtCKKbjRlPA].mp4")
        kaynak = self.core.DOWNLOADS / "Klix" / "_ListeDisi" / "001 - Sarki [xtCKKbjRlPA].mp4"
        self.dosya("Euro3", "004 - Sarki [xtCKKbjRlPA].mp4", boyut=1_900_000)
        girdi = self.core.find_duplicates(self.conn, include_inactive=True)["disk_dupes"]["xtCKKbjRlPA"]
        self.assertEqual(len(girdi["paths"]), 2)
        self.assertTrue(any("_ListeDisi" in r for r in girdi["relative_paths"]))

    # --- KAPI 3: tekil kopya ---
    def _iki_playlistte_ayni_video(self):
        self.entries["PL1"] = [("VVVVVVVVVV1", "Sarki")]
        self.entries["PL2"] = [("VVVVVVVVVV1", "Sarki")]
        self.euro_fp = self.dosya("Euro3", "001 - Sarki [VVVVVVVVVV1].mp4")
        self.satir("VVVVVVVVVV1", "Euro3", file_path=self.euro_fp)
        # Klix kaydi var ama dosyasi SILINMIS (kullanicinin durumu)
        self.satir("VVVVVVVVVV1", "Klix", file_path="Klix\\001 - Sarki [VVVVVVVVVV1].mp4")
        self.kapiyi_tak()

    def _klix_dosyasi_var_mi(self):
        return any(self.core.DOWNLOADS.joinpath("Klix").rglob("*[[]VVVVVVVVVV1[]]*")) \
            if (self.core.DOWNLOADS / "Klix").exists() else False

    def test_kopya_kaldir_dosyasiz_kayitta_hata_vermez_ve_kalici(self):
        self._iki_playlistte_ayni_video()
        ok, msg = self.core.kopya_kaldir(self.conn, "VVVVVVVVVV1", self.pid("Klix"))
        self.assertTrue(ok, msg)
        for _ in range(2):                                   # iki tarama: geri gelmemeli
            self.core.sync_two_phase(self.conn, log=lambda m: None)
        sat = self.conn.execute("SELECT p.alias FROM videos v JOIN playlists p ON p.id=v.playlist_id "
                                "WHERE v.video_id='VVVVVVVVVV1'").fetchall()
        self.assertEqual([r[0] for r in sat], ["Euro3"])
        self.assertFalse(self._klix_dosyasi_var_mi())
        self.assertTrue((self.core.DOWNLOADS / self.euro_fp).exists())     # ana kopya duruyor
        self.assertEqual(self.indirme, [])

    def test_kontrol_kopya_kaldir_olmadan_klix_geri_gelir(self):
        # kapinin NEDEN gerekli oldugunu kanitlar: yalniz DELETE yetmez, tarama geri getirir
        self._iki_playlistte_ayni_video()
        self.conn.execute("DELETE FROM videos WHERE playlist_id=?", (self.pid("Klix"),))
        self.conn.commit()
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        n = self.conn.execute("SELECT COUNT(*) FROM videos WHERE video_id='VVVVVVVVVV1'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_kopya_kaldir_paylasilan_dosyaya_dokunmaz(self):
        self._iki_playlistte_ayni_video()
        # Klix kaydi yanlislikla Euro3'un dosyasina bagli: dosya silinmemeli
        self.conn.execute("UPDATE videos SET file_path=? WHERE playlist_id=?", (self.euro_fp, self.pid("Klix")))
        self.conn.commit()
        ok, _ = self.core.kopya_kaldir(self.conn, "VVVVVVVVVV1", self.pid("Klix"))
        self.assertTrue(ok)
        self.assertTrue((self.core.DOWNLOADS / self.euro_fp).exists())

    def test_kopya_kaldir_ayni_dosyanin_kayitsiz_bag_adlarini_da_siler(self):
        """Silinen dosyanin sabit bagli diger adi (DB'de kaydi yok) 'DB disi' artik olarak kalmamali."""
        import os
        fp = self.dosya("Klix", "158 - Sarki [SSSSSSSSSS1].mp4")
        self.satir("SSSSSSSSSS1", "Klix", status="removed_from_playlist", file_path=fp)
        kardes = self.core.DOWNLOADS / "Klix" / "_ListeDisi" / "158 - Sarki [SSSSSSSSSS1].mp4"
        kardes.parent.mkdir(parents=True)
        os.link(self.core.DOWNLOADS / fp, kardes)
        (kardes.parent / "158 - Sarki [SSSSSSSSSS1].webp").write_bytes(b"w")          # kardesin yan dosyasi
        baska = self.dosya("Klix", "159 - Baska [SSSSSSSSSS1].mp4", boyut=10)           # AYRI fiziksel dosya
        ok, msg = self.core.kopya_kaldir(self.conn, "SSSSSSSSSS1", self.pid("Klix"))
        self.assertTrue(ok, msg)
        self.assertFalse((self.core.DOWNLOADS / fp).exists())
        self.assertFalse(kardes.exists(), "ayni dosyanin kayitsiz adi kalmamali")
        self.assertFalse(kardes.with_suffix(".webp").exists(), "kardesin yan dosyasi da gitmeli")
        self.assertTrue((self.core.DOWNLOADS / baska).exists(), "ayri fiziksel dosyaya dokunulmaz")

    def test_kopya_kaldir_baska_kaydin_kullandigi_bag_adina_dokunmaz(self):
        import os
        fp = self.dosya("Klix", "158 - Sarki [SSSSSSSSSS2].mp4")
        self.satir("SSSSSSSSSS2", "Klix", status="removed_from_playlist", file_path=fp)
        ad2 = self.core.DOWNLOADS / "Euro3" / "004 - Sarki [SSSSSSSSSS2].mp4"
        ad2.parent.mkdir(parents=True)
        os.link(self.core.DOWNLOADS / fp, ad2)
        self.satir("SSSSSSSSSS2", "Euro3", file_path="Euro3\\004 - Sarki [SSSSSSSSSS2].mp4")
        ok, msg = self.core.kopya_kaldir(self.conn, "SSSSSSSSSS2", self.pid("Klix"))
        self.assertTrue(ok, msg)
        self.assertTrue(ad2.exists(), "baska DB kaydinin adi silinmemeli")

    def test_playlistten_kalkmis_kayit_haric_listesine_yazilmaz(self):
        self.satir("VVVVVVVVVV1", "Klix", status="removed_from_playlist", file_path=None)
        ok, _ = self.core.kopya_kaldir(self.conn, "VVVVVVVVVV1", self.pid("Klix"))
        self.assertTrue(ok)
        tablo = self.conn.execute("SELECT name FROM sqlite_master WHERE name='playlist_haric'").fetchone()
        n = self.conn.execute("SELECT COUNT(*) FROM playlist_haric").fetchone()[0] if tablo else 0
        self.assertEqual(n, 0)


    # --- KAPI 4: yarim indirmeyi surdur ---
    def test_yarim_indirme_bulunur_ve_formati_zorlayarak_surdurulur(self):
        self.dosya("Klix", "002 - Uzun [YYYYYYYYYY1].f137.mp4")
        self.dosya("Klix", "002 - Uzun [YYYYYYYYYY1].temp.mp4")
        self.dosya("Klix", "003 - Tam [YYYYYYYYYY2].mp4")             # bitmis kopya var
        self.dosya("Klix", "003 - Tam [YYYYYYYYYY2].f251.webm")       # -> yarim degil, artik kalinti
        self.satir("YYYYYYYYYY1", "Klix", file_path="Klix\\002 - Uzun [YYYYYYYYYY1].f137.mp4")
        liste = self.core.yarim_indirmeler(self.conn)
        self.assertEqual([o["video_id"] for o in liste], ["YYYYYYYYYY1"])
        o = liste[0]
        self.assertEqual((o["idler"], o["index"], o["playlist_id"]), ([137], 2, self.pid("Klix")))
        self.assertEqual(self.core.yarim_format([137]), "137+ba/bv*+ba/b")
        self.assertEqual(self.core.yarim_format([251]), "bv*+251/bv*+ba/b")
        self.assertEqual(self.core.yarim_format([401, 251]), "401+251/bv*+ba/b")

        gorulen = []
        cls = self.core._CappedYoutubeDL

        def sahte(url, dest, idx):
            gorulen.append((url, Path(dest).name, idx, cls.FORMAT_ZORLA))
            p = Path(dest) / "002 - Uzun [YYYYYYYYYY1].mp4"
            p.write_bytes(b"x" * 2_000_000)
            return str(p)

        self.core.download_video = sahte
        ok, msg = self.core.yarim_indirmeyi_surdur(self.conn, o)
        self.assertTrue(ok, msg)
        self.assertEqual(gorulen, [("https://www.youtube.com/watch?v=YYYYYYYYYY1", "Klix", 2, "137+ba/bv*+ba/b")])
        self.assertIsNone(cls.FORMAT_ZORLA)                           # zorlama geri alindi
        fp = self.conn.execute("SELECT file_path FROM videos WHERE video_id='YYYYYYYYYY1'").fetchone()[0]
        self.assertEqual(fp, "Klix\\002 - Uzun [YYYYYYYYYY1].mp4")

    def test_yarim_indirme_basarisizsa_kayit_degismez(self):
        self.dosya("Klix", "002 - Uzun [YYYYYYYYYY1].f137.mp4")
        self.satir("YYYYYYYYYY1", "Klix", file_path="Klix\\002 - Uzun [YYYYYYYYYY1].f137.mp4")
        o = self.core.yarim_indirmeler(self.conn)[0]
        self.core.download_video = lambda *a, **k: None
        ok, _ = self.core.yarim_indirmeyi_surdur(self.conn, o)
        self.assertFalse(ok)
        fp = self.conn.execute("SELECT file_path FROM videos WHERE video_id='YYYYYYYYYY1'").fetchone()[0]
        self.assertEqual(fp, "Klix\\002 - Uzun [YYYYYYYYYY1].f137.mp4")
        self.assertIsNone(self.core._CappedYoutubeDL.FORMAT_ZORLA)


    # --- KAPI 5: yayindan kalkanlar ---
    def test_arsiv_klasoru_iki_adi_da_taniyor(self):
        r = self.core._cached.REMOVED_SUBDIR
        self.assertEqual(str(r), "_ListeDisi")                       # cekirdek yeni tasimalari buraya yapar
        self.assertEqual(str(Path("a") / r), str(Path("a") / "_ListeDisi"))
        for ad in ("_ListeDisi", "_YayındanKalkan", "_silinenler"):  # ucu de arsiv klasoru sayilir
            self.assertIn(r, Path(f"Klix/{ad}/x.mp4").parts, ad)
        self.assertNotIn(r, Path("Klix/x.mp4").parts)
        self.assertNotIn(r, Path("Klix/_inceleme/x.mp4").parts)

    def _yayindan_kalkan_kur(self):
        self.entries["PL1"] = [("UUUUUUUUUU1", "[Deleted video]")]
        fp = self.dosya("Klix", "001 - Sarki [UUUUUUUUUU1].mp4")
        self.satir("UUUUUUUUUU1", "Klix", status="unavailable", file_path=fp)
        self.conn.execute("UPDATE videos SET title='Sarki' WHERE video_id='UUUUUUUUUU1'")
        self.conn.commit()
        self.kapiyi_tak()

    def _olay(self, tip):
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (tip,)).fetchone()[0]

    def test_yayindan_kalkan_ilk_taramada_tasinir_sonra_tekrar_listelenmez(self):
        self._yayindan_kalkan_kur()
        s1 = self.core.sync_two_phase(self.conn, log=lambda m: None)
        sat = self.conn.execute("SELECT status, file_path FROM videos WHERE video_id='UUUUUUUUUU1'").fetchone()
        self.assertEqual(sat[0], "unavailable")
        self.assertEqual(Path(sat[1]).parent.name, "_YayındanKalkan")
        self.assertTrue((self.core.DOWNLOADS / sat[1]).exists())
        self.assertFalse(list((self.core.DOWNLOADS / "Klix").glob("*.mp4")))           # klasorde kalmadi
        self.assertEqual(s1["yayindan_kalkan_tasinan"], 1)
        self.assertEqual(self._olay("moved_to_yayindan_kalkan"), 1)
        reset1, kalk1 = self._olay("status_reset_file_present"), self._olay("became_unavailable")
        for _ in range(2):                                                              # sonraki taramalar sessiz
            s = self.core.sync_two_phase(self.conn, log=lambda m: None)
            self.assertEqual(s.get("unavailable", 0), 0)
            self.assertEqual(s["yayindan_kalkan_tasinan"], 0)
        self.assertEqual(self._olay("status_reset_file_present"), reset1)
        self.assertEqual(self._olay("became_unavailable"), kalk1)
        sat2 = self.conn.execute("SELECT status, file_path FROM videos WHERE video_id='UUUUUUUUUU1'").fetchone()
        self.assertEqual(tuple(sat2), tuple(sat))                                       # yerinde, unavailable

    # --- UST DUZEY ARSIV: downloads\_ListeDisi / downloads\_YayındanKalkan (playlist altinda DEGIL) ---
    def _ust_arsiv_kur(self):
        """Klix listesinde KALAN + GIDEN iki aktif, dosyali video."""
        self.entries["PL1"] = [("KEEPKEEPKE1", "Kalan")]
        for vid, ad in (("KEEPKEEPKE1", "001 - Kalan"), ("GONEGONEGO1", "002 - Giden")):
            fp = self.dosya("Klix", f"{ad} [{vid}].mp4")
            self.satir(vid, "Klix", file_path=fp)
            self.conn.execute("UPDATE videos SET title=? WHERE video_id=?", (ad[6:], vid))
        self.conn.commit()
        self.kapiyi_tak()

    def _sat(self, vid, alias="Klix"):
        return tuple(self.conn.execute("SELECT status, file_path FROM videos WHERE video_id=? AND playlist_id=?",
                                       (vid, self.pid(alias))).fetchone())

    def test_ust_arsiv_listeden_cikan_downloads_altina_gider_ve_kararli_kalir(self):
        self._ust_arsiv_kur()
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        st, fp = self._sat("GONEGONEGO1")
        self.assertEqual(st, "removed_from_playlist")
        hedef = self.core.DOWNLOADS / "_ListeDisi"
        self.assertEqual((self.core.DOWNLOADS / fp).parent, hedef, fp)
        self.assertTrue((self.core.DOWNLOADS / fp).exists())
        self.assertFalse((self.core.DOWNLOADS / "Klix" / "_ListeDisi").exists(), "playlist altinda arsiv klasoru olmamali")
        self.assertFalse(list((self.core.DOWNLOADS / "Klix").glob("*Giden*")))
        ham = (self._sat("KEEPKEEPKE1"), len(list(hedef.iterdir())))
        def say():                                              # PL2 (bos test listesi) ag-hata olayi sayilmaz
            return self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type<>'meta_fetch_failed'").fetchone()[0]
        olaylar = say()
        for _ in range(2):                                      # sonraki taramalar sessiz (durum/dosya titresmez)
            self.core.sync_two_phase(self.conn, log=lambda m: None)
        self.assertEqual(self._sat("GONEGONEGO1"), (st, fp))
        self.assertEqual((self._sat("KEEPKEEPKE1"), len(list(hedef.iterdir()))), ham)
        self.assertEqual(say(), olaylar)

    def test_ust_arsiv_listeye_geri_donen_playlist_klasorune_doner(self):
        self._ust_arsiv_kur()
        self.core.sync_two_phase(self.conn, log=lambda m: None)             # GONE arsive gitti
        self.entries["PL1"] = [("KEEPKEEPKE1", "Kalan"), ("GONEGONEGO1", "Giden")]   # listeye geri dondu
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        st, fp = self._sat("GONEGONEGO1")
        self.assertEqual(st, "active")
        self.assertEqual((self.core.DOWNLOADS / fp).parent, self.core.DOWNLOADS / "Klix", fp)
        self.assertTrue((self.core.DOWNLOADS / fp).exists())
        self.assertFalse(list(self.core.DOWNLOADS.glob("*.mp4")), "downloads kokune dosya dusmemeli")
        self.assertFalse(list((self.core.DOWNLOADS / "_ListeDisi").glob("*Giden*")))

    def test_ust_arsiv_yayindan_kalkan_downloads_altina_gider(self):
        self._yayindan_kalkan_kur()
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        st, fp = self._sat("UUUUUUUUUU1")
        self.assertEqual((st, (self.core.DOWNLOADS / fp).parent), ("unavailable", self.core.DOWNLOADS / "_YayındanKalkan"))
        self.assertTrue((self.core.DOWNLOADS / fp).exists())

    def test_arsive_toparla_eski_playlist_alti_klasorleri_toplar(self):
        a = self.dosya("Klix/_ListeDisi", "001 - A [AAAAAAAAAA1].mp4")
        self.dosya("Klix/_ListeDisi", "001 - A [AAAAAAAAAA1].info.json", 10)
        b = self.dosya("Euro3/_YayındanKalkan", "002 - B [BBBBBBBBBB1].mp4")
        self.satir("AAAAAAAAAA1", "Klix", status="removed_from_playlist", file_path=a)
        self.satir("BBBBBBBBBB1", "Euro3", status="unavailable", file_path=b)
        s = self.core.arsive_toparla(self.conn, log=lambda m: None)
        self.assertEqual(s, {"unavailable": 1, "removed_from_playlist": 1})
        d = self.core.DOWNLOADS
        self.assertEqual(self._sat("AAAAAAAAAA1")[1], "_ListeDisi\\001 - A [AAAAAAAAAA1].mp4")
        self.assertTrue((d / "_ListeDisi" / "001 - A [AAAAAAAAAA1].info.json").exists(), "yan dosya da gitmeli")
        self.assertEqual(self._sat("BBBBBBBBBB1", "Euro3")[1], "_YayındanKalkan\\002 - B [BBBBBBBBBB1].mp4")
        self.assertFalse((d / "Klix" / "_ListeDisi").exists())
        self.assertFalse((d / "Euro3" / "_YayındanKalkan").exists(), "bosalan alt klasor kalkmali")
        self.assertEqual(self.core.arsive_toparla(self.conn, log=lambda m: None),
                         {"unavailable": 0, "removed_from_playlist": 0})          # tekrar: no-op

    def test_arsive_toparla_cakismada_ve_paylasilanda_dokunmaz(self):
        a = self.dosya("Klix/_ListeDisi", "001 - A [AAAAAAAAAA1].mp4")
        self.dosya("_ListeDisi", "001 - A [AAAAAAAAAA1].mp4", 5)                 # hedefte ayni ad (farkli dosya)
        self.satir("AAAAAAAAAA1", "Klix", status="removed_from_playlist", file_path=a)
        c = self.dosya("Klix/_ListeDisi", "002 - C [CCCCCCCCCC1].mp4")
        self.satir("CCCCCCCCCC1", "Klix", status="removed_from_playlist", file_path=c)
        self.satir("CCCCCCCCCC1", "Euro3", status="active", file_path=c)          # baska kayit da kullaniyor
        s = self.core.arsive_toparla(self.conn, log=lambda m: None)
        self.assertEqual(s["removed_from_playlist"], 0)
        self.assertTrue((self.core.DOWNLOADS / a).exists())
        self.assertEqual((self.core.DOWNLOADS / "_ListeDisi" / "001 - A [AAAAAAAAAA1].mp4").stat().st_size, 5)
        self.assertTrue((self.core.DOWNLOADS / c).exists())

    def test_fazla_arsiv_adlari_yalniz_dogrulanmis_olani_siler(self):
        import os
        aktif = self.dosya("Euro3", "004 - A [AAAAAAAAAA1].mp4")
        self.satir("AAAAAAAAAA1", "Euro3", file_path=aktif)
        (self.core.DOWNLOADS / "Euro3" / "004 - A [AAAAAAAAAA1].info.json").write_text("{}")
        (self.core.DOWNLOADS / "Euro3" / "004 - A [AAAAAAAAAA1].webp").write_bytes(b"resim")
        alt = self.core.DOWNLOADS / "Klix" / "_ListeDisi"
        alt.mkdir(parents=True)
        os.link(self.core.DOWNLOADS / aktif, alt / "004 - A [AAAAAAAAAA1].mp4")             # fazladan sabit bag adi
        (alt / "004 - A [AAAAAAAAAA1].info.json").write_text("{}")                          # ayni icerik
        (alt / "004 - A [AAAAAAAAAA1].webp").write_bytes(b"FARKLI")                         # farkli icerik: kalmali
        tek = self.dosya("Klix/_ListeDisi", "009 - T [TTTTTTTTTT1].mp4")                    # kayitsiz ama TEK ad: kalmali
        self.assertEqual(self.core.fazla_arsiv_adlarini_temizle(self.conn, log=lambda m: None), 2)
        self.assertFalse((alt / "004 - A [AAAAAAAAAA1].mp4").exists())
        self.assertFalse((alt / "004 - A [AAAAAAAAAA1].info.json").exists())
        self.assertTrue((alt / "004 - A [AAAAAAAAAA1].webp").exists(), "icerigi dogrulanamayan yan dosya silinmez")
        self.assertTrue((self.core.DOWNLOADS / aktif).exists(), "gercek dosya durmali")
        self.assertTrue((self.core.DOWNLOADS / tek).exists(), "tek adli dosya silinmez")

    def test_dosyasiz_user_deleted_kayit_ayni_videolarda_listelenmez(self):
        fp = self.dosya("Klix", "001 - Sarki [VVVVVVVVVV2].mp4")
        self.satir("VVVVVVVVVV2", "Klix", file_path=fp)
        self.satir("VVVVVVVVVV2", "Euro3", status="user_deleted", file_path=None)       # elle silinmis kopya
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertNotIn("VVVVVVVVVV2", d["exact"], "silinmis kopya tek basina tekrar sayilmaz")
        # ama gercek dosyasi olan ikinci kayit hala tekrar sayilir
        fp2 = self.dosya("Euro3", "004 - Sarki [VVVVVVVVVV3].mp4")
        fp3 = self.dosya("Klix", "002 - Sarki [VVVVVVVVVV3].mp4")
        self.satir("VVVVVVVVVV3", "Klix", file_path=fp3)
        self.satir("VVVVVVVVVV3", "Euro3", file_path=fp2)
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertEqual(len(d["exact"]["VVVVVVVVVV3"]), 2)

    def test_baska_listeye_tasinan_video_ayni_videolarda_kopya_sayilmaz(self):
        import os
        # Euro3'ten Klix'e tasindi: eski satir 'removed_from_playlist', dosyasi yeni kaydin dosyasiyla AYNI fiziksel dosya
        fp_yeni = self.dosya("Klix", "001 - Sarki [MMMMMMMMMM1].mp4")
        self.satir("MMMMMMMMMM1", "Klix", file_path=fp_yeni)
        eski = self.core.DOWNLOADS / "_ListeDisi" / "004 - Sarki [MMMMMMMMMM1].mp4"
        eski.parent.mkdir(parents=True, exist_ok=True)
        os.link(self.core.DOWNLOADS / fp_yeni, eski)
        self.satir("MMMMMMMMMM1", "Euro3", status="removed_from_playlist", file_path="_ListeDisi/004 - Sarki [MMMMMMMMMM1].mp4")
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertNotIn("MMMMMMMMMM1", d["exact"], "tasinan video kopya degil")
        # AYRI fiziksel dosya ise (gercek yedek kopya) hala listelenir
        fp_a = self.dosya("Klix", "002 - Sarki [MMMMMMMMMM2].mp4")
        self.satir("MMMMMMMMMM2", "Klix", file_path=fp_a)
        fp_b = self.dosya("_ListeDisi", "005 - Sarki [MMMMMMMMMM2].mp4")
        self.satir("MMMMMMMMMM2", "Euro3", status="removed_from_playlist", file_path=fp_b)
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertEqual(len(d["exact"]["MMMMMMMMMM2"]), 2)

    def test_guvenli_azalma_yalniz_baska_listede_kopyasi_olan_kayitlari_isler(self):
        U3 = "https://www.youtube.com/playlist?list=PL2"
        for vid, ad in (("CCCCCCCCCC1", "kopyali"), ("CCCCCCCCCC2", "tek"), ("CCCCCCCCCC3", "dosyasiz kardes")):
            self.satir(vid, "Euro3", file_path=self.dosya("Euro3", f"001 - {ad} [{vid}].mp4"))
        import os
        kardes = self.dosya("Klix", "001 - kopyali [CCCCCCCCCC1].mp4")
        self.satir("CCCCCCCCCC1", "Klix", file_path=kardes)
        (self.core.DOWNLOADS / "Euro3" / "001 - kopyali [CCCCCCCCCC1].mp4").unlink()
        os.link(self.core.DOWNLOADS / kardes, self.core.DOWNLOADS / "Euro3" / "001 - kopyali [CCCCCCCCCC1].mp4")   # sabit bag
        self.satir("CCCCCCCCCC3", "Klix", file_path=None)                       # kardesin dosyasi yok -> dokunma
        canli = {U3: {"ids": {"KALAN000001"}, "kismi": False}}
        # kismi okuma -> hicbiri islenmez
        self.assertEqual(self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None,
                                                         canli={U3: {"ids": {"KALAN000001"}, "kismi": True}}), 0)
        self.assertEqual(self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli), 1)
        self.assertEqual(self._sat("CCCCCCCCCC1", "Euro3")[0], "removed_from_playlist")
        self.assertEqual(self._sat("CCCCCCCCCC1", "Klix")[0], "active", "baska listedeki kopya durur")
        self.assertTrue((self.core.DOWNLOADS / kardes).exists(), "kardesin dosyasi durur")
        # kendi (ayri) adi silinmez: arsive_toparla (2b) _ListeDisi'ne tasir; dosya bos birakilmaz (yoksa cekirdek yeniden baglar)
        self.assertTrue((self.core.DOWNLOADS / "Euro3" / "001 - kopyali [CCCCCCCCCC1].mp4").exists())
        self.assertIsNotNone(self._sat("CCCCCCCCCC1", "Euro3")[1])
        self.assertTrue((self.core.DOWNLOADS / "Euro3" / "001 - tek [CCCCCCCCCC2].mp4").exists())
        self.assertEqual(self._sat("CCCCCCCCCC2", "Euro3")[0], "active", "tek kopya korumada kalir")
        self.assertEqual(self._sat("CCCCCCCCCC3", "Euro3")[0], "active", "kardesin dosyasi yoksa dokunulmaz")
        self.assertEqual(self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli), 0)   # tekrar: islem yok

    def test_kanal_adresi_video_sekmesine_cevrilir_ve_playlist_aynen_eklenir(self):
        c = self.core
        for girdi, beklenen in (
                ("https://www.youtube.com/@Kanal", "https://www.youtube.com/@Kanal/videos"),
                ("  https://www.youtube.com/@Kanal/  ", "https://www.youtube.com/@Kanal/videos"),
                ("https://www.youtube.com/@Kanal/featured", "https://www.youtube.com/@Kanal/videos"),
                ("https://www.youtube.com/@Kanal/playlists", "https://www.youtube.com/@Kanal/videos"),
                ("https://www.youtube.com/@Kanal/shorts", "https://www.youtube.com/@Kanal/shorts"),
                ("https://www.youtube.com/channel/UCabc_-123/videos?view=0", "https://www.youtube.com/channel/UCabc_-123/videos"),
                ("https://www.youtube.com/c/Ad", "https://www.youtube.com/c/Ad/videos"),
                ("https://www.youtube.com/user/Ad", "https://www.youtube.com/user/Ad/videos")):
            self.assertTrue(c.kanal_mi(girdi), girdi)
            self.assertEqual(c.kanal_url_duzenle(girdi), beklenen)
        for pl in ("https://www.youtube.com/playlist?list=PLabc", "https://www.youtube.com/watch?v=abc&list=PL1",
                   "https://example.com/@Kanal"):
            self.assertFalse(c.kanal_mi(pl), pl)
            self.assertEqual(c.kanal_url_duzenle(pl), pl)
        # tek ekleme kapisi: GUI/CLI hangi adresi verirse versin kaydedilen duzenlenmis adrestir; ayni kanal iki kez eklenmez
        pid, err = c.add_playlist(self.conn, "https://www.youtube.com/@Kanal", "kanalim")
        self.assertIsNone(err)
        self.assertEqual(self.conn.execute("SELECT url FROM playlists WHERE id=?", (pid,)).fetchone()[0],
                         "https://www.youtube.com/@Kanal/videos")
        self.assertIsNotNone(c.add_playlist(self.conn, "https://www.youtube.com/@Kanal/featured", "x")[1])

    def test_kanal_yeni_video_en_sonda_numara_alir_ve_eskiler_kaymaz(self):
        c = self.core._cached
        kanal = "https://www.youtube.com/@Kanal/videos"
        self.assertIsNone(self.core.add_playlist(self.conn, kanal, "kanalim")[1])
        canli = ["KANALVID004", "KANALVID003", "KANALVID002", "KANALVID001"]       # YouTube: en yeni basta

        def meta(url, *a, **k):
            return {"title": "kanal", "entries": [
                {"id": v, "title": "T" + v, "uploader": "u", "duration": 100,
                 "url": "https://www.youtube.com/watch?v=" + v, "playlist_index": i + 1}       # yt-dlp: en yeni = 1
                for i, v in enumerate(canli)]}

        def dl(video_url, dest_dir, playlist_index=None):
            vid = video_url.split("v=")[1]
            d = Path(dest_dir)
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{playlist_index or 0:03d} - T{vid} [{vid}].mp4"
            p.write_bytes(b"x" * 2_000_000)
            return str(p)

        self._ham_meta = meta
        self.kapiyi_tak()
        c.download_video = dl
        pid = self.pid("kanalim")

        def sira():
            return dict(self.conn.execute("SELECT video_id, playlist_index FROM videos WHERE playlist_id=?", (pid,)).fetchall())

        # oynatma listesi (kanal degil) sirasi degismez
        duz = self.core.fetch_playlist_meta("https://www.youtube.com/playlist?list=PLxyz")["entries"]
        self.assertEqual([e["id"] for e in duz], canli)
        self.assertEqual([e["playlist_index"] for e in duz], [1, 2, 3, 4])

        self.core.sync_two_phase(self.conn, playlist_id=pid, log=lambda m: None)
        ilk = {"KANALVID001": 1, "KANALVID002": 2, "KANALVID003": 3, "KANALVID004": 4}
        self.assertEqual(sira(), ilk)
        # kanala yeni video gelir (YouTube listesinde en basa): eskilerin numarasi DEGISMEZ, yenisi en sonda (5)
        canli.insert(0, "KANALVID005")
        self.core.sync_two_phase(self.conn, playlist_id=pid, log=lambda m: None)
        self.assertEqual(sira(), dict(ilk, KANALVID005=5))
        for vid, n in sira().items():
            fp = self.conn.execute("SELECT file_path FROM videos WHERE video_id=? AND playlist_id=?", (vid, pid)).fetchone()[0]
            self.assertTrue(Path(fp.replace(chr(92), "/")).name.startswith(f"{n:03d} - "), (vid, fp))

    # --- DEPOLAMA KAPISI (birincil dolunca ek depo / hepsi doluysa temiz durma) ---
    def _depo_hazirla(self, ana_mb, ek_mb):
        core = self.core
        self.ek = self.tmp / "ek_depo"
        ok, p = core.depo_ekle(self.ek)
        self.assertTrue(ok, p)
        self.bos = {"ana": ana_mb, "ek": ek_mb}

        def sahte(yol):
            return (self.bos["ek"] if core._icinde(yol, self.ek) else self.bos["ana"]) * 1024 * 1024

        core._depo_bos = sahte                # gercek diske dokunmadan bos yeri taklit eder

    def _indirme_kur(self):
        """Gercek download_video kapisi (depo_sec) calisir; yalniz en alttaki indirici sahte."""
        self.core.DOWNLOAD_PAUSE_SECONDS = (0, 0)
        self.indirilen = []

        def ham(video_url, dest_dir, playlist_index=None):
            vid = video_url.split("v=")[1]
            d = Path(dest_dir)
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{playlist_index or 0:03d} - T [{vid}].mp4"
            p.write_bytes(b"x" * 2_000_000)
            self.indirilen.append(str(p))
            return str(p)

        self.core._download_video_orig = ham
        self.core._cached.download_video = self.core.download_video

    def test_depo_ayarlari_yazma_kapisi_diger_anahtarlari_korur_ve_bozuk_dosyada_varsayilan_verir(self):
        import json
        core = self.core
        self.assertTrue(str(core.CONFIG_FILE).startswith(str(self.tmp)), core.CONFIG_FILE)     # gercek config'e dokunma
        self.assertEqual(core.depo_ayarlari(), {"ek_depolar": [], "min_bos_mb": core.DEPO_VARSAYILAN_MIN_BOS_MB})
        core.CONFIG_FILE.write_text('{"baska": 7}', encoding="utf-8")
        core.depo_ayarlari_yaz(ek_depolar=[self.tmp / "x"], min_bos_mb=500)
        self.assertEqual(json.loads(core.CONFIG_FILE.read_text(encoding="utf-8"))["baska"], 7)
        self.assertEqual(core.depo_ayarlari(), {"ek_depolar": [self.tmp / "x"], "min_bos_mb": 500})
        self.assertFalse(core.CONFIG_FILE.with_name(core.CONFIG_FILE.name + ".tmp").exists())
        self.assertEqual(core.depo_min_bos_ayarla("abc")[0], False)
        self.assertEqual(core.depo_min_bos_ayarla(-5)[0], False)
        self.assertEqual(core.depo_min_bos_ayarla(" 1024 "), (True, 1024))
        self.assertEqual(core.depo_min_bos_mb(), 1024)
        core.CONFIG_FILE.write_text("{bozuk", encoding="utf-8")
        self.assertEqual(core.depo_ayarlari(), {"ek_depolar": [], "min_bos_mb": core.DEPO_VARSAYILAN_MIN_BOS_MB})

    def test_depo_ekle_dogrular_ve_kaldir_dosyalara_dokunmaz(self):
        core = self.core
        ek = self.tmp / "ek1"
        ok, _p = core.depo_ekle(ek)
        self.assertTrue(ok)
        self.assertTrue(ek.is_dir())
        self.assertFalse(core.depo_ekle(ek)[0])                          # ayni depo iki kez eklenmez
        self.assertFalse(core.depo_ekle(core.DOWNLOADS / "ic")[0])       # birincil depo ile ic ice olamaz
        self.assertFalse(core.depo_ekle("goreli/yol")[0])                # mutlak yol sart
        self.assertFalse(core.depo_ekle(ek / "alt")[0])                  # ekli deponun icinde
        (ek / "dosya.mp4").write_bytes(b"x")
        self.assertEqual(core.depo_kaldir(self.conn, ek), (True, 0))
        self.assertTrue((ek / "dosya.mp4").exists())                     # dosyalara dokunulmaz
        self.assertEqual(core.depo_ayarlari()["ek_depolar"], [])
        self.assertFalse(core.depo_kaldir(self.conn, ek)[0])

    def test_depo_sec_birincil_doluysa_ek_depoya_yonlendirir_hepsi_doluysa_none(self):
        core = self.core
        self._depo_hazirla(ana_mb=100, ek_mb=50_000)
        klasor = core.DOWNLOADS / "Klix"
        self.assertEqual(core.depo_sec(klasor), self.ek / "Klix")        # birincil dolu -> ek depo
        self.bos["ana"] = 50_000
        self.assertEqual(core.depo_sec(klasor), klasor)                  # birincilde yer var -> hedef degismez
        self.bos.update(ana=100, ek=100)
        self.assertIsNone(core.depo_sec(klasor))                         # hicbirinde yer yok
        self.bos.update(ana=100, ek=50_000)
        shutil.rmtree(self.ek)                                           # ek surucu takili degil
        self.assertIsNone(core.depo_sec(klasor))
        self.assertEqual(core.depo_sec(self.tmp / "baska"), self.tmp / "baska")      # depo disi hedefe karismaz

    def test_birincil_depo_dolunca_indirme_ek_depoya_gider_ve_kayit_mutlak_yol_olur(self):
        core = self.core
        self._depo_hazirla(ana_mb=100, ek_mb=50_000)
        self.entries["PL1"] = [(f"DEPOVID{i:04d}", f"S{i}") for i in range(1, 4)]
        self.kapiyi_tak()
        self._indirme_kur()
        s = core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=lambda m: None)
        self.assertEqual((s["depo_yonlendirilen"], s["depo_dolu"], s["errors"]), (3, 0, 0))
        self.assertEqual(len(self.indirilen), 3)
        for yol in self.indirilen:
            self.assertTrue(core._icinde(yol, self.ek), yol)
        for (fp,) in self.conn.execute("SELECT file_path FROM videos WHERE playlist_id=?", (self.pid("Klix"),)):
            self.assertTrue(Path(fp).is_absolute() and core._icinde(fp, self.ek) and Path(fp).exists(), fp)
        # ikinci tarama: kayitlar ek depodaki dosyalari buluyor, yeniden indirme YOK
        core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=lambda m: None)
        self.assertEqual(len(self.indirilen), 3)
        self.assertEqual([x["kayit"] for x in core.depo_durumu(self.conn)], [0, 3])

    def test_hicbir_depoda_yer_yoksa_indirme_temiz_durur_hata_olayi_yazmaz_ve_sonra_yeniden_denenir(self):
        core = self.core
        self._depo_hazirla(ana_mb=100, ek_mb=100)
        self.entries["PL1"] = [(f"DEPOVID{i:04d}", f"S{i}") for i in range(1, 4)]
        self.kapiyi_tak()
        self._indirme_kur()
        satirlar = []
        s = core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=satirlar.append)
        self.assertEqual(self.indirilen, [])
        self.assertEqual((s["depo_dolu"], s["errors"]), (3, 0))
        self.assertEqual(sum("DEPO DOLU" in x for x in satirlar), 1)                 # TEK uyari
        self.assertFalse(any("HATA: indirilemedi" in x for x in satirlar))
        n = self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='download_failed'").fetchone()[0]
        self.assertEqual(n, 0)                                                        # depo dolulugu 'indirme hatasi' degil
        self.bos.update(ana=50_000)                                                   # yer acildi
        s2 = core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=lambda m: None)
        self.assertEqual(len(self.indirilen), 3)                                      # ayni tarama simdi indirir
        self.assertEqual((s2["depo_dolu"], s2["depo_yonlendirilen"]), (0, 0))

    def test_ek_depodaki_listeden_cikan_dosya_kendi_deposunun_arsivine_gider(self):
        core = self.core
        self._depo_hazirla(ana_mb=50_000, ek_mb=50_000)
        d = self.ek / "Klix"
        d.mkdir(parents=True)
        f = d / "001 - T [EKARSIV0001].mp4"
        f.write_bytes(b"x" * 2_000_000)
        self.satir("EKARSIV0001", "Klix", status="removed_from_playlist", file_path=str(f))
        core.arsive_toparla(self.conn, log=lambda m: None)
        yeni = self.conn.execute("SELECT file_path FROM videos WHERE video_id='EKARSIV0001'").fetchone()[0]
        self.assertEqual(Path(yeni).parent, self.ek / "_ListeDisi")
        self.assertTrue(Path(yeni).exists())
        self.assertFalse((core.DOWNLOADS / "_ListeDisi").exists())                    # birincil depoya TASINMAZ
        self.assertEqual(core.arsive_toparla(self.conn, log=lambda m: None)["removed_from_playlist"], 0)   # tekrar: islem yok

    def test_disk_indeksi_ek_depolari_da_gezer_ve_birincil_kazanir(self):
        core = self.core
        self._depo_hazirla(ana_mb=50_000, ek_mb=50_000)
        (self.ek / "Klix").mkdir(parents=True)
        e1 = self.ek / "Klix" / "001 - T [INDEKS00001].mp4"
        e1.write_bytes(b"x" * 5000)
        (self.ek / "Klix" / "002 - T [INDEKS00002].mp4").write_bytes(b"x" * 5000)
        self.dosya("Klix", "003 - T [INDEKS00002].mp4")                               # ayni video birincilde de var
        (self.ek / "Klix" / "004 - T [INDEKS00003].f251.webm").write_bytes(b"x" * 5000)   # parca dosya video degil
        idx = core._cached._build_disk_index()
        self.assertEqual(Path(idx["INDEKS00001"]), e1)
        self.assertEqual(Path(idx["INDEKS00002"]).parent, core.DOWNLOADS / "Klix")    # birincil kazanir
        self.assertNotIn("INDEKS00003", idx)

    # --- 4+ HANELI SIRA NUMARASI (kanal 1000+ video) ---
    def test_dort_haneli_sira_numarasi_taninir_ve_her_taramada_yeniden_adlandirilmaz(self):
        c = self.core._cached
        self.assertEqual(c._extract_meta_from_filename("2228 - T [abcdefghijk]"), ("abcdefghijk", 2228, "T"))
        self.assertEqual(c._extract_meta_from_filename("001 - T [abcdefghijk]"), ("abcdefghijk", 1, "T"))
        fp = self.dosya("Klix", "2228 - T [IDXDORT0001].mp4")
        self.satir("IDXDORT0001", "Klix", file_path=fp)
        self.conn.execute("UPDATE videos SET playlist_index=2228 WHERE video_id='IDXDORT0001'")
        self.conn.commit()

        def meta(url, *a, **k):
            return {"title": "k", "entries": [{"id": "IDXDORT0001", "title": "T", "uploader": "u", "duration": 100,
                                               "url": "https://www.youtube.com/watch?v=IDXDORT0001", "playlist_index": 2228}]}

        self._ham_meta = meta
        self.kapiyi_tak()
        for tur in range(3):                                         # her taramada AYNI ad kalmali
            satirlar = []
            self.core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=satirlar.append)
            self.assertFalse(any("yeniden adlandirildi" in x for x in satirlar), (tur, satirlar))
            self.assertEqual(sorted(p.name for p in (self.core.DOWNLOADS / "Klix").iterdir()), ["2228 - T [IDXDORT0001].mp4"])

    def test_tekrarli_sira_numarasi_oneki_onarilir_yan_dosyalarla_ve_db_yolu_guncellenir(self):
        core = self.core
        klasor = core.DOWNLOADS / "Klix"
        fp = self.dosya("Klix", "2228 - 2228 - 2228 - 2228 - T [IDTEKRAR001].mp4")
        (klasor / "2228 - 2228 - 2228 - 2228 - T [IDTEKRAR001].info.json").write_bytes(b"{}")
        temiz = self.dosya("Klix", "005 - T [IDTEMIZ0001].mp4")
        self.satir("IDTEKRAR001", "Klix", file_path=fp)
        self.satir("IDTEMIZ0001", "Klix", file_path=temiz)
        self.conn.execute("UPDATE videos SET playlist_index=2228 WHERE video_id='IDTEKRAR001'")
        self.conn.commit()
        self.assertEqual(core.numara_tekrarlarini_onar(self.conn, log=lambda m: None), 1)
        self.assertEqual(sorted(p.name for p in klasor.iterdir()),
                         ["005 - T [IDTEMIZ0001].mp4", "2228 - T [IDTEKRAR001].info.json", "2228 - T [IDTEKRAR001].mp4"])
        yeni = self.conn.execute("SELECT file_path FROM videos WHERE video_id='IDTEKRAR001'").fetchone()[0]
        self.assertTrue(core.resolve_video_path(yeni).exists(), yeni)
        self.assertEqual(core.numara_tekrarlarini_onar(self.conn, log=lambda m: None), 0)       # tekrar: islem yok
    def test_kopya_satiri_dosyasiz_ya_da_kardesle_ayni_yolsa_kendi_arsiv_adini_alir_ve_donguye_girmez(self):
        import os
        kardes = self.dosya("Klix", "001 - Sarki [DDDDDDDDDD1].mp4")
        self.satir("DDDDDDDDDD1", "Klix", file_path=kardes)
        self.satir("DDDDDDDDDD1", "Euro3", status="removed_from_playlist", file_path=None)           # dosyasiz (eski hata)
        self.satir("DDDDDDDDDD2", "Klix", file_path=self.dosya("Klix", "002 - Sarki2 [DDDDDDDDDD2].mp4"))
        ayni = self._sat("DDDDDDDDDD2", "Klix")[1]
        self.satir("DDDDDDDDDD2", "Euro3", status="removed_from_playlist", file_path=ayni)           # kardesle AYNI yol
        self.assertEqual(self.core.kopya_satirlarini_sabitle(self.conn, log=lambda m: None), 2)
        for vid in ("DDDDDDDDDD1", "DDDDDDDDDD2"):
            st, fp = self._sat(vid, "Euro3")
            self.assertEqual(st, "removed_from_playlist")
            self.assertEqual((self.core.DOWNLOADS / fp).parent, self.core.DOWNLOADS / "_ListeDisi", fp)
            self.assertTrue((self.core.DOWNLOADS / fp).exists())
            self.assertNotEqual(fp, self._sat(vid, "Klix")[1])
            self.assertTrue(os.path.samefile(self.core.DOWNLOADS / fp, self.core.DOWNLOADS / self._sat(vid, "Klix")[1]))
        # ikinci kez: degisiklik yok (kararli)
        self.assertEqual(self.core.kopya_satirlarini_sabitle(self.conn, log=lambda m: None), 0)
        # tarama bunlari yeniden 'active' yapip olay uretmemeli
        once = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.entries["PL1"] = []
        self.entries["PL2"] = []
        self.kapiyi_tak()
        self.core.sync_two_phase(self.conn, log=lambda m: None)
        self.assertEqual(self._sat("DDDDDDDDDD1", "Euro3")[0], "removed_from_playlist")
        self.assertEqual(self._sat("DDDDDDDDDD2", "Euro3")[0], "removed_from_playlist")

    def test_coklu_silinme_kullaniciya_sorulur_kendiliginden_karar_verilmez(self):
        U3 = "https://www.youtube.com/playlist?list=PL2"
        for vid in ("EEEEEEEEE01", "EEEEEEEEE02", "EEEEEEEEE03"):
            self.satir(vid, "Euro3", file_path=self.dosya("Euro3", f"001 - x [{vid}].mp4"))
        canli = {U3: {"ids": {"KALAN000001"}, "kismi": False}}
        self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli)
        bek = self.core.silinme_bekleyenler(self.conn)
        self.assertEqual(sorted(b["video_id"] for b in bek), ["EEEEEEEEE01", "EEEEEEEEE02", "EEEEEEEEE03"])
        self.assertTrue(all(b["dosya_var"] and b["playlist_alias"] == "Euro3" for b in bek))
        for vid in ("EEEEEEEEE01", "EEEEEEEEE02", "EEEEEEEEE03"):
            self.assertEqual(self._sat(vid, "Euro3")[0], "active", "onay gelene dek dokunulmaz")
        # ikinci tarama ayni kayitlari cogaltmaz
        self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli)
        self.assertEqual(len(self.core.silinme_bekleyenler(self.conn)), 3)
        pid = self.pid("Euro3")
        # biri onaylanir -> listeden cikmis; biri korunur -> aktif kalir ve bir daha sorulmaz; biri bekler
        self.assertEqual(self.core.silinmeyi_onayla(self.conn, [(pid, "EEEEEEEEE01")], log=lambda m: None), 1)
        self.assertEqual(self.core.silinmeyi_koru(self.conn, [(pid, "EEEEEEEEE02")]), 1)
        self.assertEqual(self._sat("EEEEEEEEE01", "Euro3")[0], "removed_from_playlist")
        self.assertEqual(self._sat("EEEEEEEEE02", "Euro3")[0], "active")
        self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli)
        self.assertEqual([b["video_id"] for b in self.core.silinme_bekleyenler(self.conn)], ["EEEEEEEEE03"])
        self.assertEqual(len(self.core.silinme_bekleyenler(self.conn, korunanlar_da=True)), 2)
        # video listeye geri donunce kayit (korunan dahil) silinir: tekrar kaybolursa yeniden sorulur
        canli2 = {U3: {"ids": {"KALAN000001", "EEEEEEEEE02", "EEEEEEEEE03"}, "kismi": False}}
        self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli2)
        self.assertEqual(self.core.silinme_bekleyenler(self.conn, korunanlar_da=True), [])
        self.core.guvenli_azalmayi_isle(self.conn, log=lambda m: None, canli=canli)
        self.assertEqual(sorted(b["video_id"] for b in self.core.silinme_bekleyenler(self.conn)), ["EEEEEEEEE02", "EEEEEEEEE03"])
        # kismi okumada soru uretilmez
        self.core.silinmeyi_koru(self.conn, [(pid, "EEEEEEEEE02"), (pid, "EEEEEEEEE03")])
        self.assertEqual(self.core.silinme_bekleyenler(self.conn), [])

    def test_silinme_onayi_yazma_kapisi_gui_ve_cli_dogrudan_yazmaz(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            for yasak in ("FROM silinme_bekleyen", "INTO silinme_bekleyen", "UPDATE silinme_bekleyen", "TABLE IF NOT EXISTS silinme"):
                self.assertNotIn(yasak, src, f"{ad}: onay tablosuna dogrudan erisiyor ({yasak})")
            self.assertNotIn("removed_from_playlist' WHERE", src)
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("INSERT OR IGNORE INTO silinme_bekleyen"), 1)

    def test_farkli_kesim_surumu_ses_karariyla_ayni_videolarda_eslesir(self):
        def ekle(vid, alias, baslik, sure, boyut):
            fp = self.dosya(alias, f"001 - x [{vid}].mp4", boyut)
            self.satir(vid, alias, file_path=fp)
            self.conn.execute("UPDATE videos SET title=?, duration=? WHERE video_id=? AND playlist_id=?",
                              (baslik, sure, vid, self.pid(alias)))
            self.conn.commit()
            return self.core.resolve_video_path(fp)
        a = ekle("SURUMAAAAA1", "Klix", "Dj Ramezz & DAR - Stereo Girl (Club extended mix)", 228, 1_100_000)
        b = ekle("SURUMBBBBB1", "Euro3", 'Dj Ramezz "Stereo Girl " 2026 (Dance Edit)', 199, 1_200_000)       # sure farki 29 sn
        c = ekle("SURUMCCCCC1", "Klix", "Dj Ramezz - Baska Sarki", 228, 1_300_000)                           # baslik farkli
        self.core._ses_tablolari(self.conn)

        def grup_var():
            f = self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"]
            return any({x["video_id"] for x in rows} >= {"SURUMAAAAA1", "SURUMBBBBB1"} for rows in f.values())

        self.assertFalse(grup_var(), "ses karari yokken (sure farki >3 sn) eslesmez")
        ham = self.core._find_duplicates_baslik(self.conn, include_inactive=True, surum_adaylari=True)
        self.assertTrue(any({x["video_id"] for x in rows} >= {"SURUMAAAAA1", "SURUMBBBBB1"} for rows in ham["fuzzy"].values()),
                        "ses_analizi'ne giden HAM gruplarda aday olmali")
        self.assertFalse(any("SURUMCCCCC1" in {x["video_id"] for x in rows} for rows in ham["fuzzy"].values()))
        ka, kb = self.core._ses_kimlik(a, a.stat().st_size), self.core._ses_kimlik(b, b.stat().st_size)
        x, y = self.core._ses_cifti((ka, a, a.stat().st_size), (kb, b, b.stat().st_size))
        self.core._ses_cift_yaz(self.conn, x[0], y[0], x[2], y[2], 0.55, 0.5)                                # ses 'farkli'
        self.conn.commit()
        self.assertFalse(grup_var(), "ses 'farkli' dediyse eslesmez")
        self.core._ses_cift_yaz(self.conn, x[0], y[0], x[2], y[2], 0.72, 0.7)                                # ses 'benzer'
        self.conn.commit()
        self.assertTrue(grup_var(), "ses 'benzer' (>=0.68) ise farkli kesim surumu eslesir")

    # --- BILINMEYEN DOSYALAR: veritabaninda bagi olmayan her sey downloads/_Bilinmeyen/ ---
    def _bil_kur(self):
        import os
        D = self.core.DOWNLOADS
        bagli = self.dosya("Klix", "001 - Bagli [AAAAAAAAAA1].mp4")
        self.satir("AAAAAAAAAA1", "Klix", file_path=bagli)
        (D / "Klix" / "001 - Bagli [AAAAAAAAAA1].info.json").write_text("{}")                 # bagli videonun yan dosyasi: KALIR
        (D / "Klix" / "009 - Yetim [ZZZZZZZZZZ9].webp").write_text("x")                       # sahipsiz yan dosya: TASINIR
        (D / "Klix" / "003 - Yarim [PPPPPPPPPP1].f137.mp4").write_bytes(b"1234")              # yarim indirme parcasi: KALIR
        (D / "Klix" / "004 - Yarim [PPPPPPPPPP2].mp4.part").write_bytes(b"1234")              # .part: KALIR
        (D / "Music").mkdir()
        os.link(D / bagli, D / "Music" / "001 - Bagli [AAAAAAAAAA1].mp4")                      # bagli dosyanin fazladan adi: TASINIR
        (D / "Music" / "050 - Kayitsiz [KKKKKKKKKK1].mp4").write_bytes(b"k" * 2000)           # DB'de hic yok: TASINIR
        (D / "Music" / "050 - Kayitsiz [KKKKKKKKKK1].webp").write_text("x")                   # onun yan dosyasi: TASINIR
        self.satir("BBBBBBBBBB1", "Klix", file_path=None)                                       # dosyasi olmayan kayit
        (D / "Music" / "060 - Baglanabilir [BBBBBBBBBB1].mp4").write_bytes(b"b" * 2000)       # kaydi var, dosyasi yok: baglama icin KALIR
        return D

    def test_bilinmeyen_baglanti_olmayan_her_dosya_tasinir_gerisine_dokunulmaz(self):
        D = self._bil_kur()
        plan = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None, kuru=True)
        self.assertEqual(sorted(p.name for p, _t in plan["dosyalar"]),
                         sorted(["009 - Yetim [ZZZZZZZZZZ9].webp", "001 - Bagli [AAAAAAAAAA1].mp4",
                                 "050 - Kayitsiz [KKKKKKKKKK1].mp4", "050 - Kayitsiz [KKKKKKKKKK1].webp"]))
        self.assertFalse((D / "_Bilinmeyen").exists(), "kuru calistirma hicbir sey tasimaz")
        o = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None)
        self.assertEqual((o["tasinan_video"], o["tasinan_yan"], o["atlanan_baglanabilir"], o["atlanan_gecici"]), (2, 2, 1, 2))
        self.assertEqual(sorted(x.name for x in (D / "_Bilinmeyen").iterdir()),
                         sorted(["009 - Yetim [ZZZZZZZZZZ9].webp", "001 - Bagli [AAAAAAAAAA1].mp4",
                                 "050 - Kayitsiz [KKKKKKKKKK1].mp4", "050 - Kayitsiz [KKKKKKKKKK1].webp"]))
        for kalan in ("Klix/001 - Bagli [AAAAAAAAAA1].mp4", "Klix/001 - Bagli [AAAAAAAAAA1].info.json",
                      "Klix/003 - Yarim [PPPPPPPPPP1].f137.mp4", "Klix/004 - Yarim [PPPPPPPPPP2].mp4.part",
                      "Music/060 - Baglanabilir [BBBBBBBBBB1].mp4"):
            self.assertTrue((D / kalan).exists(), kalan)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='moved_to_bilinmeyen'").fetchone()[0], 2)
        # ikinci calistirma: yapacak is yok
        o2 = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None)
        self.assertEqual((o2["tasinan_video"], o2["tasinan_yan"]), (0, 0))

    def test_bilinmeyen_bos_kalan_klasor_silinir_playlist_klasoru_silinmez(self):
        D = self.core.DOWNLOADS
        (D / "Music").mkdir()
        (D / "Music" / "050 - Kayitsiz [KKKKKKKKKK1].mp4").write_bytes(b"k" * 2000)
        (D / "Klix").mkdir(exist_ok=True)
        (D / "Klix" / "009 - Yetim [ZZZZZZZZZZ9].webp").write_text("x")
        self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None)
        self.assertFalse((D / "Music").exists(), "eski klasor bosaldi, silinir")
        self.assertTrue((D / "Klix").is_dir(), "kayitli playlist klasoru bos kalsa da durur")

    def test_bilinmeyen_toplu_tasima_siradisi_buyukse_durur(self):
        D = self.core.DOWNLOADS
        (D / "Eski").mkdir()
        for i in range(25):
            (D / "Eski" / f"{i:03d} - X [XXXXXXXXX{i:02d}].mp4").write_bytes(b"x" * 100)
        bagli = self.dosya("Klix", "001 - Bagli [AAAAAAAAAA1].mp4")
        self.satir("AAAAAAAAAA1", "Klix", file_path=bagli)
        o = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None)
        self.assertIsNotNone(o["durdu"])
        self.assertEqual(len(list((D / "Eski").iterdir())), 25, "durdu: hicbir sey tasinmadi")
        o = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None, zorla=True)
        self.assertEqual(o["tasinan_video"], 25)

    def test_alt_cizgili_klasorler_otomatik_koleksiyon_sekmesi_bilinmeyen_dahil(self):
        D = self.core.DOWNLOADS
        (D / "Music").mkdir()
        (D / "Music" / "050 - Kayitsiz [KKKKKKKKKK1].mp4").write_bytes(b"k" * 2000)
        (D / "_Favoriler").mkdir()
        (D / "_Favoriler" / "001 - F [FFFFFFFFFF1].mp4").write_bytes(b"f" * 2000)
        o = self.core.manuel_tarama(self.conn, log=lambda m: None)
        self.assertEqual(o["bilinmeyen"]["tasinan_video"], 1)
        adlar = sorted(c["alias"] for c in self.core.list_manual_collections(self.conn))
        self.assertEqual(adlar, ["Bilinmeyen", "Favoriler"], "her '_' klasoru (yeni _Bilinmeyen dahil) otomatik sekme")
        bil = next(c for c in self.core.list_manual_collections(self.conn) if c["alias"] == "Bilinmeyen")
        self.assertEqual([r["video_id"] for r in self.core.get_collection_videos(self.conn, bil["id"])], ["KKKKKKKKKK1"])
        # kayitli oldugu icin tekrar taramada yeniden tasinmaz / kopyalanmaz
        o2 = self.core.manuel_tarama(self.conn, log=lambda m: None)
        self.assertEqual(o2["bilinmeyen"]["tasinan_video"], 0)
        self.assertEqual(len(list((D / "_Bilinmeyen").iterdir())), 1)

    def test_tum_klasorleri_tara_bilinmeyeni_tasir(self):
        D = self._bil_kur()
        s = self.core.tum_klasorleri_tara(self.conn, log=lambda m: None)
        self.assertEqual(s["bilinmeyen"]["tasinan_video"], 2)
        self.assertTrue((D / "_Bilinmeyen" / "050 - Kayitsiz [KKKKKKKKKK1].mp4").exists())

    def test_ayni_fiziksel_dosyayi_gosteren_iki_satir_ayni_videolarda_kopya_sayilmaz(self):
        import os
        D = self.core.DOWNLOADS
        a = self.dosya("Klix", "001 - S [HHHHHHHHHH1].mp4")
        self.satir("HHHHHHHHHH1", "Klix", file_path=a)
        (D / "_Bilinmeyen").mkdir()
        os.link(D / a, D / "_Bilinmeyen" / "001 - S [HHHHHHHHHH1].mp4")
        self.satir("HHHHHHHHHH1", "Euro3", file_path="_Bilinmeyen/001 - S [HHHHHHHHHH1].mp4")
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertNotIn("HHHHHHHHHH1", d["exact"], "ayni inode = tek fiziksel dosya, tekrar degil")
        # farkli fiziksel dosya ise hala tekrar
        b = self.dosya("Klix", "002 - S2 [HHHHHHHHHH2].mp4")
        c = self.dosya("Euro3", "004 - S2 [HHHHHHHHHH2].mp4")
        self.satir("HHHHHHHHHH2", "Klix", file_path=b)
        self.satir("HHHHHHHHHH2", "Euro3", file_path=c)
        d = self.core.find_duplicates(self.conn, include_inactive=True)
        self.assertEqual(len(d["exact"]["HHHHHHHHHH2"]), 2)

    def test_bilinmeyen_cevrimdisi_arsiv_klasorlerine_dokunmaz(self):
        D = self.core.DOWNLOADS
        for ad in ("_ListeDisi", "_YayındanKalkan"):
            (D / ad).mkdir()
            (D / ad / "001 - Arsiv [ARSIVARSIV1].mp4").write_bytes(b"a" * 2000)          # kaydi olmayan arsiv dosyasi
        (D / "Klix").mkdir()
        (D / "Klix" / "009 - Yetim [ZZZZZZZZZZ9].webp").write_text("x")
        o = self.core.bilinmeyenleri_tasi(self.conn, log=lambda m: None)
        self.assertEqual((o["tasinan_video"], o["tasinan_yan"]), (0, 1))
        for ad in ("_ListeDisi", "_YayındanKalkan"):
            self.assertTrue((D / ad / "001 - Arsiv [ARSIVARSIV1].mp4").exists(), ad)

    def test_yayindan_kalkanlar_okuma_kapisi(self):
        self.satir("UUUUUUUUUU1", "Klix", status="unavailable", file_path=None)
        self.satir("UUUUUUUUUU2", "Euro3", status="unavailable", file_path=None)
        self.satir("UUUUUUUUUU3", "Klix", status="active", file_path=None)
        self.core.log_event(self.conn, "became_unavailable", "UUUUUUUUUU1", self.pid("Klix"), "T")
        self.conn.commit()
        liste = self.core.yayindan_kalkanlar(self.conn)
        self.assertEqual(sorted(r["video_id"] for r in liste), ["UUUUUUUUUU1", "UUUUUUUUUU2"])
        r1 = next(r for r in liste if r["video_id"] == "UUUUUUUUUU1")
        self.assertEqual(r1["playlist_alias"], "Klix")
        self.assertTrue(r1["kalkis"])
        self.assertIsNone(next(r for r in liste if r["video_id"] == "UUUUUUUUUU2")["kalkis"])

    def test_yayindan_kalkan_ayni_video_eslesmesi(self):
        def ekle(vid, alias, baslik, sure, status, dosyali=True):
            fp = self.dosya(alias, f"001 - X [{vid}].mp4", 1_500_000) if dosyali else None
            self.satir(vid, alias, status=status, file_path=fp)
            self.conn.execute("UPDATE videos SET title=?, duration=? WHERE video_id=? AND playlist_id=?",
                              (baslik, sure, vid, self.pid(alias)))
            self.conn.commit()
        # kalkan: "Hot Love" - canli ikizi baslik farkli ama sure ayni
        ekle("KKKKKKKKKK1", "Klix", "Eurodance Energy 2026 | DJ RAMEZZ | Hot Love", 216, "unavailable")
        ekle("AAAAAAAAAA1", "Euro3", 'Dj Ramezz "Hot Love" 2026 (Italo Dance Version)', 216, "active")
        # kalkan: ayni baslik yapisi ama sure cok farkli -> eslesmez
        ekle("KKKKKKKKKK2", "Klix", "Night eurodance 2025", 281, "unavailable")
        ekle("AAAAAAAAAA2", "Euro3", "Night eurodance 2026", 300, "active")
        # kalkan: tek kopya
        ekle("KKKKKKKKKK3", "Klix", "Tamamen Baska Bir Sarki", 150, "unavailable")
        # kalkan ama dosyasi yok -> es aranmaz
        ekle("KKKKKKKKKK4", "Klix", "Eurodance Energy 2026 | DJ RAMEZZ | Hot Love", 216, "unavailable", dosyali=False)
        liste = {r["video_id"]: r for r in self.core.yayindan_kalkanlar(self.conn)}
        self.assertEqual(liste["KKKKKKKKKK1"]["es"]["video_id"], "AAAAAAAAAA1")
        self.assertIn(liste["KKKKKKKKKK1"]["es"]["guc"], ("guclu", "ayni"))
        self.assertGreater(liste["KKKKKKKKKK1"]["boyut"], 0)
        self.assertIsNone(liste["KKKKKKKKKK2"]["es"])
        self.assertIsNone(liste["KKKKKKKKKK3"]["es"])
        self.assertIsNone(liste["KKKKKKKKKK4"]["es"])
        self.assertEqual(liste["KKKKKKKKKK4"]["boyut"], 0)

    def test_yayindan_kalkan_silme_kalici_ve_ikiz_kalir(self):
        fp_k = self.dosya("Klix", "001 - X [KKKKKKKKKK1].mp4")
        fp_a = self.dosya("Euro3", "001 - X [AAAAAAAAAA1].mp4")
        self.satir("KKKKKKKKKK1", "Klix", status="unavailable", file_path=fp_k)
        self.satir("AAAAAAAAAA1", "Euro3", status="active", file_path=fp_a)
        ok, _ = self.core.kopya_kaldir(self.conn, "KKKKKKKKKK1", self.pid("Klix"))
        self.assertTrue(ok)
        self.assertFalse((self.core.DOWNLOADS / fp_k).exists())
        self.assertTrue((self.core.DOWNLOADS / fp_a).exists())
        self.assertEqual(self.core.yayindan_kalkanlar(self.conn), [])

    def test_yayindan_kalkan_paylasilan_dosya_tasinmaz(self):
        fp = self.dosya("Klix", "001 - Sarki [UUUUUUUUUU1].mp4")
        self.satir("UUUUUUUUUU1", "Klix", status="unavailable", file_path=fp)
        self.satir("UUUUUUUUUU1", "Euro3", status="active", file_path=fp)               # ayni dosya
        self.assertEqual(self.core.yayindan_kalkanlari_tasi(self.conn, log=lambda m: None), 0)
        self.assertTrue((self.core.DOWNLOADS / fp).exists())


    # --- KAPI 3b: basliksiz (ozel/kaldirilmis) kayitlar ---
    def _basliksiz_kur(self, adet=1, dosyali=False):
        self.entries["PL1"] = [(f"BBBBBBBBBB{i}", None) for i in range(adet)]
        for i in range(adet):
            fp = self.dosya("Klix", f"{i + 1:03d} - X [BBBBBBBBBB{i}].mp4") if dosyali else None
            self.satir(f"BBBBBBBBBB{i}", "Klix", file_path=fp)
            self.conn.execute("UPDATE videos SET title='' WHERE video_id=?", (f"BBBBBBBBBB{i}",))
        self.conn.commit()
        self.kapiyi_tak()
        self.sorgu = []

    def _tara(self, n=1):
        sonuc = []
        for _ in range(n):
            log = []
            s = self.core.sync_two_phase(self.conn, playlist_id=self.pid("Klix"), log=log.append)
            sonuc.append((s, log))
        return sonuc

    def test_basliksiz_ozel_video_yayindan_kalkmis_sayilir_ve_hata_vermez(self):
        self._basliksiz_kur()
        self.core._video_durumu = lambda vid: ("kalkmis", "ERROR: [youtube] x: Private video")
        sonuc = self._tara(3)
        durum = self.conn.execute("SELECT status FROM videos WHERE video_id='BBBBBBBBBB0'").fetchone()[0]
        self.assertEqual(durum, "unavailable")
        for s, _ in sonuc:
            self.assertEqual((s.get("errors", 0), s.get("deferred", 0)), (0, 0))
        kalkmis = sum("YAYINDAN KALKMIS" in l for _, log in sonuc for l in log)
        self.assertEqual(kalkmis, 1)                                  # yalniz ilk taramada, bir kez
        self.assertEqual(self.indirme, [])

    def test_basliksiz_canli_video_basligi_doldurulur(self):
        self._basliksiz_kur(dosyali=True)
        self.core._video_durumu = lambda vid: ("ok", {"title": "Gercek Ad", "duration": 100, "uploader": "U"})
        self._tara(1)
        sat = self.conn.execute("SELECT status, title FROM videos WHERE video_id='BBBBBBBBBB0'").fetchone()
        self.assertEqual(tuple(sat), ("active", "Gercek Ad"))

    def test_basliksiz_dogrulanamazsa_dokunulmaz(self):
        self._basliksiz_kur(dosyali=True)
        self.core._video_durumu = lambda vid: ("belirsiz", "ag hatasi")
        self._tara(1)
        sat = self.conn.execute("SELECT status, title FROM videos WHERE video_id='BBBBBBBBBB0'").fetchone()
        self.assertEqual(tuple(sat), ("active", ""))

    def test_cok_basliksiz_kayitta_dogrulama_yapilmaz(self):
        self._basliksiz_kur(adet=self.core._BASLIKSIZ_ESIK + 1, dosyali=True)
        cagri = []
        self.core._video_durumu = lambda vid: cagri.append(vid) or ("kalkmis", "Private video")
        self._tara(1)
        self.assertEqual(cagri, [])                                   # bozuk cekim: toplu isaretleme yok
        n = self.conn.execute("SELECT COUNT(*) FROM videos WHERE status='unavailable'").fetchone()[0]
        self.assertEqual(n, 0)


    # --- KAVRAMLAR: SEN listeden cikardin (_ListeDisi)  !=  YOUTUBE kaldirdi (_YayındanKalkan) ---
    def test_liste_disi_ve_yayindan_kalkan_ayri_klasorlere_gider(self):
        self.entries["PL1"] = [("CCCCCCCCCC0", "S0"), ("CCCCCCCCCC2", "[Deleted video]"), ("CCCCCCCCCC3", "S3"),
                               ("CCCCCCCCCC4", "S4"), ("CCCCCCCCCC5", "S5")]          # C1 listede YOK (sen cikardin)
        for i in range(6):
            fp = self.dosya("Klix", f"{i + 1:03d} - S{i} [CCCCCCCCCC{i}].mp4")
            self.satir(f"CCCCCCCCCC{i}", "Klix", file_path=fp)
        self.kapiyi_tak()
        (s, log), = self._tara(1)
        sat = {r[0]: r[1:] for r in self.conn.execute("SELECT video_id, status, file_path FROM videos")}
        d1, d2 = sat["CCCCCCCCCC1"], sat["CCCCCCCCCC2"]
        self.assertEqual(d1[0], "removed_from_playlist")
        self.assertEqual(Path(d1[1]).parent.name, "_ListeDisi")
        self.assertEqual(d2[0], "unavailable")
        self.assertEqual(Path(d2[1]).parent.name, "_YayındanKalkan")
        for fp in (d1[1], d2[1]):
            self.assertTrue((self.core.DOWNLOADS / fp).exists())
        metin = "\n".join(log)
        self.assertIn("LISTEDEN CIKARILMIS", metin)
        self.assertNotIn("PLAYLISTTEN SILINMIS", metin)
        self.assertNotIn("_silinenler/", metin)

    def test_eski_silinenler_klasoru_liste_disi_ya_tasinir(self):
        eski = self.dosya("Klix\\_silinenler", "001 - X [DDDDDDDDDD1].mp4")
        self.satir("DDDDDDDDDD1", "Klix", status="removed_from_playlist", file_path=eski)
        self.assertEqual(self.core.liste_disi_klasorlerini_tasi(self.conn, log=lambda m: None), 1)
        yeni = "Klix\\_ListeDisi\\001 - X [DDDDDDDDDD1].mp4"
        self.assertEqual(self.conn.execute("SELECT file_path FROM videos").fetchone()[0], yeni)
        self.assertTrue((self.core.DOWNLOADS / yeni).exists())
        self.assertFalse((self.core.DOWNLOADS / "Klix" / "_silinenler").exists())
        self.assertEqual(self.core.liste_disi_klasorlerini_tasi(self.conn, log=lambda m: None), 0)   # tekrar: no-op

    def test_klasor_gecisinde_cakisan_ad_ezilmez(self):
        eski = self.dosya("Klix\\_silinenler", "001 - X [DDDDDDDDDD1].mp4", 1_000_000)
        self.dosya("Klix\\_ListeDisi", "001 - X [DDDDDDDDDD1].mp4", 2_000_000)                       # ayni ad, baska icerik
        self.satir("DDDDDDDDDD1", "Klix", status="removed_from_playlist", file_path=eski)
        self.core.liste_disi_klasorlerini_tasi(self.conn, log=lambda m: None)
        self.assertTrue((self.core.DOWNLOADS / eski).exists())                                       # kaynak yerinde
        self.assertEqual((self.core.DOWNLOADS / "Klix" / "_ListeDisi" / "001 - X [DDDDDDDDDD1].mp4").stat().st_size,
                         2_000_000)                                                                  # hedef ezilmedi
        self.assertEqual(self.conn.execute("SELECT file_path FROM videos").fetchone()[0], eski)

    def test_liste_disi_youtube_durumu_onbellegi(self):
        self.satir("EEEEEEEEEE1", "Klix", status="removed_from_playlist")
        self.satir("EEEEEEEEEE2", "Klix", status="removed_from_playlist")
        cagri = []

        def sahte(vid):
            cagri.append(vid)
            return ("kalkmis", "Private video") if vid.endswith("1") else ("belirsiz", "ag hatasi")

        self.core._video_durumu = sahte
        self.assertEqual(self.core.liste_disi_youtube_durumunu_yenile(self.conn, log=lambda m: None), 1)
        self.assertEqual(self.core.youtube_durumlari(self.conn), {"EEEEEEEEEE1": "kalkmis"})
        cagri.clear()
        self.core.liste_disi_youtube_durumunu_yenile(self.conn, log=lambda m: None)
        self.assertEqual(cagri, ["EEEEEEEEEE2"])          # bilinen tekrar yoklanmaz, belirsiz olan tekrar denenir
        # durum/dosya degismez: bu yalnizca bilgidir
        n = self.conn.execute("SELECT COUNT(*) FROM videos WHERE status='removed_from_playlist'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_yayindan_kalkanlar_youtube_yoklamasi(self):
        self.satir("UUUUUUUUUU1", "Klix", status="unavailable")
        self.satir("UUUUUUUUUU2", "Klix", status="unavailable")
        self.satir("UUUUUUUUUU3", "Klix", status="unavailable")
        self.satir("AAAAAAAAAA1", "Klix", status="active")                       # unavailable degil: yoklanmaz
        cagri = []

        def sahte(vid):
            cagri.append(vid)
            return {"UUUUUUUUUU1": ("ok", {}), "UUUUUUUUUU2": ("kalkmis", "Private video")}.get(vid, ("belirsiz", "bot"))

        self.core._video_durumu = sahte
        s = self.core.yayindan_kalkanlari_yokla(self.conn, log=lambda m: None, bekle=(0, 0))
        self.assertEqual(cagri, ["UUUUUUUUUU1", "UUUUUUUUUU2", "UUUUUUUUUU3"])
        self.assertEqual((s["yayinda"], s["kalkmis"], s["belirsiz"]), (1, 1, 1))
        self.assertEqual([v for v, _ in s["geri_gelen"]], ["UUUUUUUUUU1"])
        self.assertEqual(self.core.youtube_durumlari(self.conn), {"UUUUUUUUUU1": "yayinda", "UUUUUUUUUU2": "kalkmis"})
        # yalniz bilgi: durum degismez
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM videos WHERE status='unavailable'").fetchone()[0], 3)
        # GUI okuma kapisi durumu tasir
        by = {r["video_id"]: r for r in self.core.yayindan_kalkanlar(self.conn)}
        self.assertEqual(by["UUUUUUUUUU1"]["yt_durum"], "yayinda")
        self.assertEqual(by["UUUUUUUUUU2"]["yt_durum"], "kalkmis")
        self.assertIsNone(by["UUUUUUUUUU3"]["yt_durum"])
        # secili kimlikler: yalniz onlar
        cagri.clear()
        self.core.yayindan_kalkanlari_yokla(self.conn, video_idleri=["UUUUUUUUUU3"], log=lambda m: None, bekle=(0, 0))
        self.assertEqual(cagri, ["UUUUUUUUUU3"])

    def test_yayindan_kalkan_yoklama_ust_uste_belirsizde_durur_ve_iptal_eder(self):
        for i in range(6):
            self.satir(f"BBBBBBBBBB{i}", "Klix", status="unavailable")
        cagri = []
        self.core._video_durumu = lambda vid: cagri.append(vid) or ("belirsiz", "Sign in to confirm you're not a bot")
        s = self.core.yayindan_kalkanlari_yokla(self.conn, log=lambda m: None, bekle=(0, 0))
        self.assertEqual(len(cagri), 3, "bot kontrolunde ust uste yoklayip hesabi yormamali")
        self.assertEqual(self.core.youtube_durumlari(self.conn), {})
        cagri.clear()
        self.core._video_durumu = lambda vid: cagri.append(vid) or ("ok", {})
        s = self.core.yayindan_kalkanlari_yokla(self.conn, log=lambda m: None, bekle=(0, 0),
                                                should_cancel=lambda: len(cagri) >= 2)
        self.assertTrue(s["iptal"])
        self.assertEqual(len(cagri), 2)

    def test_ayni_video_gruplari_tum_klasorleri_kapsar(self):
        def ekle(vid, alias, klasor, baslik, status):
            fp = self.dosya(klasor, f"001 - X [{vid}].mp4", 1_500_000)
            self.satir(vid, alias, status=status, file_path=fp)
            self.conn.execute("UPDATE videos SET title=?, duration=216 WHERE video_id=?", (baslik, vid))
            self.conn.commit()
        ekle("FFFFFFFFFF1", "Klix", "Klix", 'Dj Ramezz "Hot Love" 2026 (Italo Dance Version)', "active")
        ekle("FFFFFFFFFF2", "Euro3", "Euro3\\_ListeDisi", "Eurodance Energy 2026 | DJ RAMEZZ | Hot Love", "removed_from_playlist")
        ekle("FFFFFFFFFF3", "Klix", "Klix\\_YayındanKalkan", "Dj Ramezz - Hot Love (Official Video)", "unavailable")
        sadece_aktif = self.core.find_duplicates(self.conn, include_inactive=False)["fuzzy"]
        self.assertFalse([k for k in sadece_aktif if k.startswith("~")])
        tum = self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"]
        gruplar = [v for k, v in tum.items() if k.startswith("~")]
        self.assertEqual(len(gruplar), 1)
        self.assertEqual({e["video_id"] for e in gruplar[0]}, {"FFFFFFFFFF1", "FFFFFFFFFF2", "FFFFFFFFFF3"})
        etiketler = {e["video_id"]: e["playlist_alias"] for e in gruplar[0]}
        self.assertIn("[liste disi]", etiketler["FFFFFFFFFF2"])
        self.assertIn("[yayindan kalkan]", etiketler["FFFFFFFFFF3"])


class SesKapisi(unittest.TestCase):
    """Ses parmak izi kapisi: fpcalc sahtedir (gercek arac/ag/DB'ye dokunulmaz)."""

    def setUp(self):
        import random
        self.tmp = Path(tempfile.mkdtemp(prefix="ytarsiv_ses_"))
        for f in ("ytarsiv_core.py", "ytarsiv_core_cached.pyc"):
            shutil.copy2(HERE / f, self.tmp / f)
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        sys.path.insert(0, str(self.tmp))
        self.core = importlib.import_module("ytarsiv_core")
        c = self.core._cached
        c.DOWNLOADS = self.tmp / "downloads"
        c.DOWNLOADS.mkdir()
        self.core.DOWNLOADS = c.DOWNLOADS
        self.assertTrue(str(c.DB_PATH).startswith(str(self.tmp)), c.DB_PATH)
        self.conn = self.core.init_db()
        rnd = random.Random(1)
        self.a = [rnd.getrandbits(32) for _ in range(948)]
        self.kayik = [rnd.getrandbits(32) for _ in range(40)] + self.a[:-40]      # ayni kayit, 5 sn gec baslar
        self.c = [rnd.getrandbits(32) for _ in range(948)]                        # alakasiz
        self.izler = {"a.mp4": (200, self.a), "b.mp4": (205, self.kayik), "c.mp4": (200, self.c)}
        self.cagri = []

        def sahte(yol, zaman_asimi=180):
            self.cagri.append(Path(yol).name)
            sonuc = self.izler[Path(yol).name]
            if sonuc is None:
                raise ValueError("bozuk dosya")
            return sonuc
        self.core._fpcalc_iz = sahte

    def tearDown(self):
        self.conn.close()
        sys.path.remove(str(self.tmp))
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dosya(self, ad, boyut=1000):
        d = self.core.DOWNLOADS / "ListeA"
        d.mkdir(exist_ok=True)
        (d / ad).write_bytes(b"x" * boyut)
        return {"file_path": f"ListeA\\{ad}"}

    def gruplar(self, *adlar):
        return {"exact": {}, "fuzzy": {"x": [self.dosya(a) for a in adlar]}, "disk_dupes": {}, "disk_orphans": {}}

    def test_benzerlik_ayni_kayit_kaymayi_asar_rastgele_dusuk(self):
        s, blok = self.core._iz_benzerligi(self.a, self.a)
        self.assertGreater(s, 0.99)
        s, blok = self.core._iz_benzerligi(self.a, self.kayik)
        self.assertGreater(s, 0.99, "5 sn kayan ayni kayit yakalanmali")
        s, _ = self.core._iz_benzerligi(self.a, self.c)
        self.assertLess(s, 0.62)

    def test_etiketler_esikleri(self):
        e = self.core.ses_etiketi
        self.assertEqual(e(0.96, 0.95), "ayni")
        self.assertEqual(e(0.96, 0.50), "benzer", "bloklar tutarsizsa 'ayni' denmez")
        self.assertEqual(e(0.70, 0.7), "benzer")
        self.assertEqual(e(0.58, 0.6), "farkli")
        self.assertEqual(e(None), "farkli")

    def test_analiz_yazar_onbellekler_ve_boyut_degisince_yeniler(self):
        dups = self.gruplar("a.mp4", "b.mp4", "c.mp4")
        o = self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        self.assertEqual((o["cift"], o["ayni"], o["farkli"], o["hata"]), (3, 1, 2, 0))
        self.assertEqual(sorted(self.cagri), ["a.mp4", "b.mp4", "c.mp4"], "her dosya yalniz bir kez okunmali")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ses_iz").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ses_eslesme").fetchone()[0], 3)
        # ikinci kosu: hicbir sey yeniden hesaplanmaz
        self.cagri.clear()
        o = self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        self.assertEqual((self.cagri, o["yeni_cift"], o["yeni_iz"]), ([], 0, 0))
        self.assertEqual((o["ayni"], o["farkli"]), (1, 2))
        # dosya degisince (boyut) yalniz onu ilgilendirenler yenilenir
        self.dosya("c.mp4", boyut=2500)
        o = self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        self.assertEqual(self.cagri, ["c.mp4"])
        self.assertEqual(o["yeni_cift"], 2)

    def test_sureleri_cok_farkli_ciftler_karsilastirilmaz(self):
        self.izler["c.mp4"] = (900, self.c)
        sayac = []
        orig = self.core._iz_benzerligi
        self.core._iz_benzerligi = lambda x, y: sayac.append(1) or orig(x, y)
        self.core.ses_analizi(self.conn, self.gruplar("a.mp4", "c.mp4"), log=lambda m: None)
        self.assertEqual(sayac, [])
        satir = self.conn.execute("SELECT benzerlik FROM ses_eslesme").fetchone()
        self.assertIsNone(satir[0])

    def test_bozuk_dosya_digerlerini_durdurmaz_ve_yazilmaz(self):
        self.izler["c.mp4"] = None
        o = self.core.ses_analizi(self.conn, self.gruplar("a.mp4", "b.mp4", "c.mp4"), log=lambda m: None)
        self.assertEqual((o["ayni"], o["hata"]), (1, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ses_eslesme").fetchone()[0], 1)

    def test_iptal_gruplar_arasinda_durur(self):
        dups = self.gruplar("a.mp4", "b.mp4")
        o = self.core.ses_analizi(self.conn, dups, log=lambda m: None, should_cancel=lambda: True)
        self.assertTrue(o["iptal"])
        self.assertEqual(self.cagri, [])

    def test_etiketleri_okuma_tablo_olusturmaz_ve_en_iyi_cifti_verir(self):
        dups = self.gruplar("a.mp4", "b.mp4", "c.mp4")
        self.assertEqual(self.core.ses_etiketleri(self.conn, dups), {})
        tablolar = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("ses_eslesme", tablolar, "okuma kapisi tablo olusturmamali")
        self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        et = self.core.ses_etiketleri(self.conn, dups)
        yol = lambda ad: str(self.core.DOWNLOADS / "ListeA" / ad)
        self.assertEqual(et[yol("a.mp4")]["etiket"], "ayni")
        self.assertEqual(et[yol("b.mp4")]["etiket"], "ayni")
        self.assertEqual(et[yol("c.mp4")]["etiket"], "farkli")
        self.assertTrue(self.core.ses_metni(et[yol("a.mp4")]).startswith("AYNI "))
        self.assertEqual(self.core.ses_metni(None), "-")

    def _sanatci_zinciri(self, ekle=()):
        """Ayni sanatcinin 4 farkli videosu (sure ayni, basliklar 'savage 44 ...' oneki yuzunden zincirleme birlesir);
        yalniz a.mp4 ve b.mp4 gercekten ayni kayit."""
        import random
        rnd = random.Random(9)
        self.izler["a.mp4"] = (200, self.a)
        self.izler["b.mp4"] = (200, self.a)
        self.izler["c.mp4"] = (200, [rnd.getrandbits(32) for _ in range(948)])
        self.izler["d.mp4"] = (200, [rnd.getrandbits(32) for _ in range(948)])
        self.izler["e.mp4"] = (200, [rnd.getrandbits(32) for _ in range(948)])
        self.conn.execute("INSERT OR IGNORE INTO playlists (id, url, alias, title, added_at) VALUES (1,'u1','ListeA','ListeA','2026-01-01')")
        basliklar = {"a.mp4": "Hope", "b.mp4": "I love", "c.mp4": "Dawn", "d.mp4": "Faith", "e.mp4": "Fire"}
        for ad in ("a.mp4", "b.mp4", "c.mp4", "d.mp4") + tuple(ekle):
            fp = self.dosya(ad)["file_path"]
            vid = ad.split(".")[0] * 11
            self.conn.execute(
                "INSERT INTO videos (video_id, playlist_id, title, status, file_path, duration, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?)", (vid, 1, f"SAVAGE-44 - {basliklar[ad]}", "active", fp, 200, "2026-01-01", "2026-01-01"))
        self.conn.commit()

    def _fuzzy_idler(self):
        fz = self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"]
        return [sorted(e["video_id"][0] for e in v) for v in fz.values()]

    def test_sanatci_oneki_yuzunden_zincirlenen_grup_ses_analiziyle_bolunur(self):
        self._sanatci_zinciri()
        self.assertEqual(self._fuzzy_idler(), [["a", "b", "c", "d"]], "analizden once ham baslik grubu kalir")
        self.core.ses_analizi(self.conn, log=lambda m: None)
        self.assertEqual(self._fuzzy_idler(), [["a", "b"]], "yalniz gercekten ayni kayit kalmali")

    def test_analizi_eksik_grup_bolunmez_yeni_uye_gelince_yeniden_bolunur(self):
        self._sanatci_zinciri()
        self.core.ses_analizi(self.conn, log=lambda m: None)
        self.assertEqual(self._fuzzy_idler(), [["a", "b"]])
        self._sanatci_zinciri_yeni_uye = self.dosya("e.mp4")["file_path"]
        self.conn.execute(
            "INSERT INTO videos (video_id, playlist_id, title, status, file_path, duration, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?)", ("e" * 11, 1, "SAVAGE-44 - Fire", "active", self._sanatci_zinciri_yeni_uye, 200, "2026-01-01", "2026-01-01"))
        self.conn.commit()
        self.assertEqual(len(self._fuzzy_idler()[0]), 5, "analizi eksik: ham grup oldugu gibi gorunur")
        self.core.ses_analizi(self.conn, log=lambda m: None)
        self.assertEqual(self._fuzzy_idler(), [["a", "b"]])

    # --- kimlik (video_id#boyut) ve tum kutuphane ---
    def _idli(self, klasor, ad, boyut=1000):
        d = self.core.DOWNLOADS / klasor
        d.mkdir(parents=True, exist_ok=True)
        (d / ad).write_bytes(b"x" * boyut)
        return d / ad

    def _izler_vid(self, izler):
        """fpcalc'i [video_id]'ye gore sahtele (dosya adi degisse de ayni iz)."""
        def sahte(yol, zaman_asimi=180):
            vid = re.search(r"\[([A-Za-z0-9_-]{11})\]", Path(yol).stem).group(1)
            self.cagri.append(vid)
            return izler[vid]
        self.core._fpcalc_iz = sahte

    def _kayit(self, vid, baslik, klasor, ad, sure=200, playlist=1, boyut=1000):
        self._idli(klasor, ad, boyut)
        self.conn.execute("INSERT OR IGNORE INTO playlists (id, url, alias, title, added_at) VALUES (?,?,?,?,?)",
                          (playlist, f"u{playlist}", f"L{playlist}", f"L{playlist}", "2026-01-01"))
        self.conn.execute(
            "INSERT INTO videos (video_id, playlist_id, title, status, file_path, duration, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?)", (vid, playlist, baslik, "active", f"{klasor}\\{ad}", sure, "2026-01-01", "2026-01-01"))
        self.conn.commit()

    def _gruplu(self, *yollar):
        return {"exact": {}, "fuzzy": {"x": [{"file_path": y} for y in yollar]}, "disk_dupes": {}, "disk_orphans": {}}

    def test_kimlik_yeniden_adlandirma_ve_tasimada_degismez(self):
        self._izler_vid({"AAAAAAAAAAA": (200, self.a), "BBBBBBBBBBB": (200, self.a)})
        a = self._idli("ListeA", "001 - X [AAAAAAAAAAA].mp4")
        b = self._idli("ListeA", "002 - Y [BBBBBBBBBBB].mp4")
        self.core.ses_analizi(self.conn, self._gruplu("ListeA\\001 - X [AAAAAAAAAAA].mp4", "ListeA\\002 - Y [BBBBBBBBBBB].mp4"),
                              log=lambda m: None)
        self.assertEqual(len(self.cagri), 2)
        # yeniden numaralama + _ListeDisi'ne tasima (cekirdegin yaptigi islemler)
        a.rename(a.with_name("007 - X [AAAAAAAAAAA].mp4"))
        (self.core.DOWNLOADS / "ListeA" / "_ListeDisi").mkdir()
        b.rename(self.core.DOWNLOADS / "ListeA" / "_ListeDisi" / "002 - Y [BBBBBBBBBBB].mp4")
        yeni = self._gruplu("ListeA\\007 - X [AAAAAAAAAAA].mp4", "ListeA\\_ListeDisi\\002 - Y [BBBBBBBBBBB].mp4")
        et = self.core.ses_etiketleri(self.conn, yeni)
        self.assertEqual(len(et), 2, "analiz kaybolmamali")
        self.assertTrue(all(e["etiket"] == "ayni" for e in et.values()))
        self.cagri.clear()
        o = self.core.ses_analizi(self.conn, yeni, log=lambda m: None)
        self.assertEqual((self.cagri, o["yeni_cift"]), ([], 0), "ad degisince yeniden hesaplanmamali")

    def test_sabit_bagli_adlar_ayni_dosyanin_etiketini_alir(self):
        import os
        self._izler_vid({"AAAAAAAAAAA": (200, self.a), "BBBBBBBBBBB": (200, self.a)})
        a = self._idli("ListeA", "001 - X [AAAAAAAAAAA].mp4")
        bag = self.core.DOWNLOADS / "ListeB"
        bag.mkdir()
        os.link(a, bag / "005 - X [AAAAAAAAAAA].mp4")                       # ayni dosyanin ikinci adi
        self._idli("ListeA", "002 - Y [BBBBBBBBBBB].mp4")
        dups = self._gruplu("ListeA\\001 - X [AAAAAAAAAAA].mp4", "ListeB\\005 - X [AAAAAAAAAAA].mp4",
                            "ListeA\\002 - Y [BBBBBBBBBBB].mp4")
        o = self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        self.assertEqual((o["cift"], o["hata"]), (1, 0), "bagli adlar tek dugum: kendisiyle karsilastirilmaz")
        et = self.core.ses_etiketleri(self.conn, dups)
        self.assertEqual(len(et), 3, "her ad etiket almali")

    def test_eski_yol_anahtarli_kayitlar_kimlige_tasinir(self):
        import array
        self._idli("ListeA", "001 - X [AAAAAAAAAAA].mp4", 1000)
        self._idli("ListeA", "002 - Y [BBBBBBBBBBB].mp4", 1000)
        self.conn.execute("CREATE TABLE ses_izi (yol TEXT PRIMARY KEY, boyut INTEGER, sure INTEGER, iz BLOB, hesaplandi TEXT)")
        self.conn.execute("CREATE TABLE ses_cift (yol_a TEXT, yol_b TEXT, boyut_a INTEGER, boyut_b INTEGER, benzerlik REAL, "
                          "blok REAL, hesaplandi TEXT, PRIMARY KEY (yol_a, yol_b))")
        blob = array.array("I", self.a).tobytes()
        for yol in ("ListeA/001 - X [AAAAAAAAAAA].mp4", "ListeA/002 - Y [BBBBBBBBBBB].mp4"):
            self.conn.execute("INSERT INTO ses_izi VALUES (?,?,?,?,?)", (yol, 1000, 200, blob, "t"))
        self.conn.execute("INSERT INTO ses_cift VALUES (?,?,?,?,?,?,?)",
                          ("ListeA/001 - X [AAAAAAAAAAA].mp4", "ListeA/002 - Y [BBBBBBBBBBB].mp4", 1000, 1000, 0.97, 0.97, "t"))
        self.conn.commit()
        self.assertEqual(self.core._ses_goc(self.conn), 2)
        self.assertEqual(self.core._ses_goc(self.conn), 0, "ikinci kosu dokunmamali")
        et = self.core.ses_etiketleri(self.conn, self._gruplu("ListeA\\001 - X [AAAAAAAAAAA].mp4", "ListeA\\002 - Y [BBBBBBBBBBB].mp4"))
        self.assertEqual({e["etiket"] for e in et.values()}, {"ayni"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ses_izi").fetchone()[0], 2, "eski tablo silinmemeli")

    def _kutuphane_kur(self):
        self._izler_vid({"AAAAAAAAAAA": (200, self.a), "BBBBBBBBBBB": (200, self.a),
                         "CCCCCCCCCCC": (200, self.c), "DDDDDDDDDDD": (2000, self.a)})
        self._kayit("AAAAAAAAAAA", "Alpha Song", "ListeA", "001 - Alpha [AAAAAAAAAAA].mp4", 200, 1, 1000)
        self._kayit("BBBBBBBBBBB", "Zebra Quartet Live", "ListeB", "050 - Zebra [BBBBBBBBBBB].mp4", 200, 2, 1100)
        self._kayit("CCCCCCCCCCC", "Mango Tango", "ListeA", "002 - Mango [CCCCCCCCCCC].mp4", 200, 1, 1200)
        self._kayit("DDDDDDDDDDD", "Long Mix Set", "ListeB", "051 - Long [DDDDDDDDDDD].mp4", 2000, 2, 1300)

    def test_kutuphane_analizi_basligi_farkli_ayni_kaydi_bulur(self):
        self._kutuphane_kur()
        self.assertEqual(self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"], {}, "basliklar benzemiyor")
        o = self.core.ses_kutuphane_analizi(self.conn, log=lambda m: None)
        self.assertEqual((o["dosya"], o["yeni_iz"], o["hata"]), (4, 4, 0))
        self.assertEqual((o["aday"], o["yeni_cift"], o["ayni"]), (1, 1, 1), "yalniz A-B: C alakasiz, D suresi uzak")
        gruplar = [sorted(e["video_id"] for e in v) for v in
                   self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"].values()]
        self.assertEqual(gruplar, [["AAAAAAAAAAA", "BBBBBBBBBBB"]], "ses grubu listeye eklenmeli")
        # ikinci kosu: hicbir sey yeniden hesaplanmaz
        self.cagri.clear()
        o = self.core.ses_kutuphane_analizi(self.conn, log=lambda m: None)
        self.assertEqual((self.cagri, o["yeni_iz"], o["yeni_cift"]), ([], 0, 0))

    def test_kutuphane_grubu_mevcut_baslik_grubunu_ikilemez(self):
        self._izler_vid({"AAAAAAAAAAA": (200, self.a), "BBBBBBBBBBB": (200, self.a)})
        self._kayit("AAAAAAAAAAA", "Same Song", "ListeA", "001 - Same [AAAAAAAAAAA].mp4", 200, 1)
        self._kayit("BBBBBBBBBBB", "Same Song", "ListeB", "050 - Same [BBBBBBBBBBB].mp4", 200, 2)
        self.assertEqual(len(self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"]), 1)
        self.core.ses_kutuphane_analizi(self.conn, log=lambda m: None)
        gruplar = self.core.find_duplicates(self.conn, include_inactive=True)["fuzzy"]
        self.assertEqual(len(gruplar), 1, "ayni grup iki kez listelenmemeli")

    def test_kutuphane_analizi_iptal_ve_kaldigi_yerden_devam(self):
        self._kutuphane_kur()
        sayac = {"n": 0}

        def iptal():
            sayac["n"] += 1
            return sayac["n"] > 2                            # iki dosyadan sonra iptal
        o = self.core.ses_kutuphane_analizi(self.conn, log=lambda m: None, should_cancel=iptal)
        self.assertTrue(o["iptal"])
        self.assertEqual(o["yeni_iz"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ses_iz").fetchone()[0], 2, "cikarilan izler kayitli")
        self.cagri.clear()
        o = self.core.ses_kutuphane_analizi(self.conn, log=lambda m: None)
        self.assertEqual(len(self.cagri), 2, "yalniz kalan 2 dosyanin izi cikarilir")
        self.assertFalse(o["iptal"])

    def test_aday_bulma_alakasiz_ve_uzak_sureli_cifti_aday_yapmaz(self):
        izler = {"A#1": (200, self.a), "B#1": (200, self.kayik), "C#1": (200, self.c), "D#1": (2000, self.a)}
        self.assertEqual(self.core._aday_ciftler(izler), [("A#1", "B#1")])

    def test_analiz_dosya_veya_durum_degistirmez(self):
        dups = self.gruplar("a.mp4", "b.mp4")
        once = sorted(p.name for p in (self.core.DOWNLOADS / "ListeA").iterdir())
        self.core.ses_analizi(self.conn, dups, log=lambda m: None)
        self.assertEqual(once, sorted(p.name for p in (self.core.DOWNLOADS / "ListeA").iterdir()))


class BagKapisi(unittest.TestCase):
    """'Mevcut dosya kullanildi' diskte yer tutmamali: kopya degil sabit bag."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ytarsiv_bag_"))
        for f in ("ytarsiv_core.py", "ytarsiv_core_cached.pyc"):
            shutil.copy2(HERE / f, self.tmp / f)
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        sys.path.insert(0, str(self.tmp))
        self.core = importlib.import_module("ytarsiv_core")
        self.A = self.tmp / "A"
        self.B = self.tmp / "B"
        self.A.mkdir()
        self.src = self.A / "001 - Sarki [AAAAAAAAAAA].mp4"
        self.src.write_bytes(b"x" * 5000)
        (self.A / "001 - Sarki [AAAAAAAAAAA].info.json").write_bytes(b"{}")

    def tearDown(self):
        sys.path.remove(str(self.tmp))
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mevcut_dosya_kullanimi_sabit_bag_kurar_yer_tutmaz(self):
        hedef = self.core.materialize_existing_video(self.src, self.B, 7)
        self.assertTrue(hedef.is_file())
        self.assertEqual(hedef.parent, self.B)
        self.assertEqual(self.src.stat().st_nlink, 2, "kopya degil sabit bag olmali")
        self.assertEqual(hedef.read_bytes(), self.src.read_bytes())
        yan = [p for p in self.B.iterdir() if p.suffix == ".json"]
        self.assertEqual(len(yan), 1, "yan dosyalar da gelmeli")

    def test_bag_olmazsa_kopyalar(self):
        import os
        gercek = os.link

        def yok(*a, **k):
            raise OSError("bag yok")
        os.link = yok
        try:
            self.B.mkdir()
            hedef = self.core._cached.shutil.copy2(self.src, self.B / "k.mp4")
        finally:
            os.link = gercek
        self.assertEqual(Path(hedef).read_bytes(), self.src.read_bytes())
        self.assertEqual(self.src.stat().st_nlink, 1, "bag yoksa gercek kopya")

    def test_yer_yoksa_kopya_bastan_reddedilir_yarim_dosya_kalmaz(self):
        import os
        import collections
        gercek_link, gercek_du = os.link, shutil.disk_usage

        def yok(*a, **k):
            raise OSError("bag yok")
        os.link = yok
        shutil.disk_usage = lambda p: collections.namedtuple("u", "total used free")(10**9, 10**9, 1000)
        try:
            self.B.mkdir()
            with self.assertRaises(OSError) as ctx:
                self.core._cached.shutil.copy2(self.src, self.B / "k.mp4")
        finally:
            os.link, shutil.disk_usage = gercek_link, gercek_du
        self.assertIn("yetersiz disk", str(ctx.exception))
        self.assertEqual(list(self.B.iterdir()), [], "yarim dosya birakilmamali")

    def test_cekirdekte_baska_copy2_yok(self):
        import dis
        import io
        import contextlib
        import types
        raw = importlib.machinery.SourcelessFileLoader("_ham_bag", str(HERE / "ytarsiv_core_cached.pyc")).load_module()
        kullanan = []
        for ad, f in vars(raw).items():
            if isinstance(f, types.FunctionType):
                b = io.StringIO()
                with contextlib.redirect_stdout(b):
                    dis.dis(f)
                if "copy2" in b.getvalue():
                    kullanan.append(ad)
        self.assertEqual(kullanan, ["materialize_existing_video"], "yeni copy2 kullanan yer: vekil yeniden gozden gecirilmeli")


class EslestirmeKapisi(unittest.TestCase):
    """Dosya eslestirme: ayni -> sabit bag, farkli -> kopya_farkli tablosu (dosyalara dokunulmaz)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ytarsiv_esle_"))
        for f in ("ytarsiv_core.py", "ytarsiv_core_cached.pyc"):
            shutil.copy2(HERE / f, self.tmp / f)
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        sys.path.insert(0, str(self.tmp))
        self.core = importlib.import_module("ytarsiv_core")
        c = self.core._cached
        c.DOWNLOADS = self.tmp / "downloads"
        c.DOWNLOADS.mkdir()
        self.core.DOWNLOADS = c.DOWNLOADS
        self.assertTrue(str(c.DB_PATH).startswith(str(self.tmp)), c.DB_PATH)
        self.conn = self.core.init_db()
        self.D = self.core.DOWNLOADS

    def tearDown(self):
        self.conn.close()
        sys.path.remove(str(self.tmp))
        for n in [m for m in sys.modules if m.startswith(("ytarsiv_core", "_ytarsiv_core"))]:
            del sys.modules[n]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def yaz(self, klasor, ad, icerik):
        d = self.D / klasor
        d.mkdir(parents=True, exist_ok=True)
        (d / ad).write_bytes(icerik)
        return d / ad

    def kur(self):
        self.a1 = self.yaz("A", "001 - X [AAAAAAAAAAA].mp4", b"a" * 4000)
        self.a2 = self.yaz("B", "005 - X [AAAAAAAAAAA].mp4", b"a" * 4000)           # birebir ayni
        self.o1 = self.yaz("A", "002 - Y [BBBBBBBBBBB].mp4", b"1" * 3000)
        self.o2 = self.yaz("B", "006 - Y [BBBBBBBBBBB].mp4", b"2" * 3000)           # ayni boyut, FARKLI icerik
        self.b1 = self.yaz("A", "003 - Z [CCCCCCCCCCC].mp4", b"z" * 2000)
        self.b2 = self.yaz("B", "007 - Z [CCCCCCCCCCC].mp4", b"z" * 2500)           # FARKLI boyut
        self.p1 = self.yaz("A", "009 - W [DDDDDDDDDDD].f251.webm", b"p" * 800)      # indirme parcasi: video degil
        self.p2 = self.yaz("B", "009 - W [DDDDDDDDDDD].f251.webm", b"p" * 800)
        self.tek = self.yaz("A", "004 - T [EEEEEEEEEEE].mp4", b"t" * 100)

    def test_ayni_baglanir_farkli_kaydedilir_diger_dosyalar_degismez(self):
        self.kur()
        o = self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.assertEqual((o["video"], o["baglandi"], o["farkli"]), (3, 1, 2))
        self.assertEqual(self.a1.stat().st_ino, self.a2.stat().st_ino, "ayni dosya sabit bagla birlesmeli")
        self.assertEqual(self.a2.read_bytes(), b"a" * 4000)
        self.assertNotEqual(self.o1.stat().st_ino, self.o2.stat().st_ino)
        self.assertEqual((self.o1.read_bytes(), self.o2.read_bytes()), (b"1" * 3000, b"2" * 3000))
        self.assertNotEqual(self.b1.stat().st_ino, self.b2.stat().st_ino)
        self.assertNotEqual(self.p1.stat().st_ino, self.p2.stat().st_ino, "indirme parcalari eslestirilmez")
        satirlar = {r["video_id"]: r["neden"] for r in self.core.farkli_kopyalar(self.conn)}
        self.assertEqual(satirlar, {"BBBBBBBBBBB": "ozet", "CCCCCCCCCCC": "boyut"})
        self.assertEqual(list(self.D.rglob("*.baglanti.tmp")), [])

    def test_ikinci_calistirma_degistirmez_ve_olay_tekrarlamaz(self):
        self.kur()
        self.core.kopyalari_esle(self.conn, log=lambda m: None)
        olay = lambda t: self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (t,)).fetchone()[0]
        once = (olay("dosya_baglandi"), olay("kopya_farkli"))
        self.assertEqual(once, (1, 2))
        o = self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.assertEqual((o["baglandi"], o["yeni_farkli"], o["farkli"]), (0, 0, 2))
        self.assertEqual((olay("dosya_baglandi"), olay("kopya_farkli")), once)

    def test_farkli_kopya_tekrar_ayni_olunca_tablodan_duser(self):
        self.kur()
        self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.o2.write_bytes(b"1" * 3000)                                             # artik birebir ayni
        o = self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.assertEqual(o["baglandi"], 1)
        self.assertEqual({r["video_id"] for r in self.core.farkli_kopyalar(self.conn)}, {"CCCCCCCCCCC"})

    def test_kilitli_dosyaya_dokunulmaz(self):
        import os
        self.kur()
        gercek = os.replace

        def kilit(*a, **k):
            raise PermissionError("kullanimda")
        os.replace = kilit
        try:
            o = self.core.kopyalari_esle(self.conn, log=lambda m: None)
        finally:
            os.replace = gercek
        self.assertEqual((o["baglandi"], o["atlandi"]), (0, 1))
        self.assertNotEqual(self.a1.stat().st_ino, self.a2.stat().st_ino)
        self.assertEqual(self.a2.read_bytes(), b"a" * 4000)
        self.assertEqual(list(self.D.rglob("*.baglanti.tmp")), [], "gecici bag artigi kalmamali")

    def test_iptal_tabloyu_bozmaz(self):
        self.kur()
        self.core.kopyalari_esle(self.conn, log=lambda m: None)
        once = self.core.farkli_kopyalar(self.conn)
        o = self.core.kopyalari_esle(self.conn, log=lambda m: None, should_cancel=lambda: True)
        self.assertTrue(o["iptal"])
        self.assertEqual(self.core.farkli_kopyalar(self.conn), once)

    def test_medya_klasoru_yoksa_hicbir_sey_yapmaz(self):
        self.core.DOWNLOADS = self.tmp / "yok"
        o = self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.assertEqual((o["video"], o["baglandi"]), (0, 0))
        self.assertFalse((self.tmp / "yok").exists())

    def test_yarim_kalmis_gecici_bag_temizlenir(self):
        self.kur()
        artik = self.yaz("A", "001 - X [AAAAAAAAAAA].mp4.baglanti.tmp", b"a" * 4000)
        self.core.kopyalari_esle(self.conn, log=lambda m: None)
        self.assertFalse(artik.exists())

    def test_sync_sonunda_calisir_iptalde_calismaz(self):
        self.core._video_durumu = lambda vid: ("belirsiz", "test: ag yok")
        cagri = []
        orig = self.core.kopyalari_esle
        self.core.kopyalari_esle = lambda *a, **k: cagri.append(1) or orig(*a, **k)
        asamalar = []
        s = self.core.sync_two_phase(self.conn, log=lambda m: None, on_phase=asamalar.append)
        self.assertEqual(len(cagri), 1)
        self.assertEqual(asamalar[-1], "Dosyalar eslestiriliyor...", "eslestirme en son asama olmali")
        self.assertIn("kopya_baglanan", s)
        s = self.core.sync_two_phase(self.conn, log=lambda m: None, should_cancel=lambda: True)
        self.assertEqual(len(cagri), 1, "iptal edilen taramada eslestirme calismamali")
        self.assertNotIn("kopya_baglanan", s)


class ListeDisiCokluSil(unittest.TestCase):
    """Liste Disi sekmesi: coklu secim + toplu silme.  GUI modulu yuklenir ama pencere acilmaz, DB'ye dokunulmaz."""

    def setUp(self):
        import importlib.util
        import os
        import types
        self.cwd = os.getcwd()
        spec = importlib.util.spec_from_file_location("gui_coklu", HERE / "YT-Arsiv-GUI.pyw")
        self.gui = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(HERE))
        spec.loader.exec_module(self.gui)
        self.rows = [
            {"video_id": "AAAAAAAAAAA", "playlist_id": 1, "playlist_alias": "Klix", "title": "Bir", "file_path": None},
            {"video_id": "BBBBBBBBBBB", "playlist_id": 2, "playlist_alias": "Euro1", "title": "Iki", "file_path": None,
             "instances": [{"playlist_id": 2, "playlist_alias": "Euro1"}, {"playlist_id": 3, "playlist_alias": "Euro2"}]},
            {"video_id": "CCCCCCCCCCC", "playlist_id": 1, "playlist_alias": "Klix", "title": "Uc", "file_path": None},
        ]
        self.logs, self.yenilendi = [], []
        g = self.gui
        self.fake = types.SimpleNamespace(conn=None, log=self.logs.append, _refresh_all=lambda: self.yenilendi.append(1))
        self.fake._selected_lost_rows = lambda: list(self.rows)
        self.fake._lost_hedefleri = lambda r: g.App._lost_hedefleri(self.fake, r)
        self.fake._lost_ozet_metni = lambda r, h: g.App._lost_ozet_metni(self.fake, r, h)
        self.onay = {"cevap": True, "metin": None}

        def sor(baslik, metin, **k):
            self.onay["metin"] = metin
            return self.onay["cevap"]
        self._eski = (g.messagebox.askyesno, g.core.kopya_kaldir, g.core.permanently_delete_video)
        g.messagebox.askyesno = sor
        self.cagrilar = []

    def tearDown(self):
        import os
        g = self.gui
        g.messagebox.askyesno, g.core.kopya_kaldir, g.core.permanently_delete_video = self._eski
        sys.path.remove(str(HERE))
        os.chdir(self.cwd)

    def test_hepsi_islenir_bir_hata_digerlerini_durdurmaz(self):
        def sahte(conn, vid, pid):
            self.cagrilar.append((vid, pid))
            if vid == "AAAAAAAAAAA":
                raise RuntimeError("disk hatasi")
            return (True, "silindi") if vid != "CCCCCCCCCCC" else (False, "kayit yok")
        self.gui.core.kopya_kaldir = sahte
        self.gui.App.on_delete_lost_permanent(self.fake)
        self.assertEqual(self.cagrilar, [("AAAAAAAAAAA", 1), ("BBBBBBBBBBB", 2), ("BBBBBBBBBBB", 3), ("CCCCCCCCCCC", 1)])
        self.assertIn("3 video, 4 DB kaydi", self.onay["metin"])
        self.assertTrue(any("2 kayit silindi, 2 hata" in m for m in self.logs), self.logs)
        self.assertEqual(self.yenilendi, [1])

    def test_onay_verilmezse_hicbir_sey_silinmez(self):
        self.onay["cevap"] = False
        self.gui.core.kopya_kaldir = lambda *a: self.cagrilar.append(a) or (True, "x")
        self.gui.core.permanently_delete_video = lambda *a, **k: self.cagrilar.append(a) or (True, "x")
        self.gui.App.on_delete_lost_permanent(self.fake)
        self.gui.App.on_delete_lost_record_only(self.fake)
        self.assertEqual(self.cagrilar, [])
        self.assertEqual(self.yenilendi, [])

    def test_secim_yoksa_uyarir_silmez(self):
        mesajlar = []
        eski = self.gui.messagebox.showinfo
        self.gui.messagebox.showinfo = lambda *a, **k: mesajlar.append(a)
        try:
            self.fake._selected_lost_rows = lambda: []
            self.gui.core.kopya_kaldir = lambda *a: self.cagrilar.append(a) or (True, "x")
            self.gui.App.on_delete_lost_permanent(self.fake)
        finally:
            self.gui.messagebox.showinfo = eski
        self.assertEqual((self.cagrilar, len(mesajlar)), ([], 1))

    def test_sadece_kayit_silme_dosyayi_korur_ve_hepsini_isler(self):
        def sahte(conn, vid, pid, delete_file=True):
            self.cagrilar.append((vid, pid, delete_file))
            return True, "ok"
        self.gui.core.permanently_delete_video = sahte
        self.gui.App.on_delete_lost_record_only(self.fake)
        self.assertEqual(len(self.cagrilar), 4)
        self.assertTrue(all(c[2] is False for c in self.cagrilar), "dosyaya dokunulmamali")

    def test_liste_coklu_secime_acik_ve_silme_kapidan_gecer(self):
        src = (HERE / "YT-Arsiv-GUI.pyw").read_text(encoding="utf-8")
        self.assertIn('self.tv_lost = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")', src)
        a = src.index("    def on_delete_lost_permanent(self):")
        b = src.index("    def _open_playlist_subdir(")
        govde = src[a:b]
        self.assertIn("core.kopya_kaldir", govde)
        self.assertNotIn("permanently_delete_video(", govde, "kalici silme kopya_kaldir kapisindan gecmeli")


class StatikKapilar(unittest.TestCase):
    def test_kanal_adresi_cozumleme_yalniz_cekirdekte(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            self.assertNotIn("/videos", src, ad)
            self.assertNotIn("re.compile", src, ad)

    def test_kanal_sirasi_yalniz_fetch_kapisinda_cevrilir(self):
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("_kanal_sirasini_cevir("), 2, "tanim + fetch_playlist_meta'daki TEK cagri")
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            self.assertNotIn("_kanal_sirasini_cevir", src, ad)
            self.assertNotIn('["playlist_index"] =', src, ad)

    def test_depo_ayari_ve_bos_yer_kontrolu_yalniz_depo_kapisinda(self):
        import re as _re
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("disk_usage("), 3, "disk_usage yalniz: _ShutilBag, _depo_bos, depo_durumu")
        self.assertEqual(core.count("os.replace(gecici, CONFIG_FILE)"), 1, "config.json TEK yazma kapisi")
        self.assertEqual(core.count("def depo_sec("), 1)
        m = _re.search(r"def download_video\(video_url, dest_dir, playlist_index=None\):.*?(?=\n\n\n)", core, _re.S)
        self.assertIsNotNone(m)
        self.assertIn("depo_sec(dest_dir)", m.group(0), "indirme depo kapisini atliyor")
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            for yasak in ("disk_usage", "config.json", "CONFIG_FILE", '"ek_depolar"', "'ek_depolar'", '"min_bos_mb"', "'min_bos_mb'"):
                self.assertNotIn(yasak, src, f"{ad}: depo ayari/bos yer kontrolu kapi disinda ({yasak})")

    def test_bilinmeyen_taramalari_yalniz_kontrolorden_cagrilir(self):
        import re as _re
        gui = (HERE / "YT-Arsiv-GUI.pyw").read_text(encoding="utf-8")
        for govde_adi, kontrolor in (("on_scan_manual", "core.manuel_tarama("), ("on_scan_local_files", "core.tum_klasorleri_tara(")):
            m = _re.search(rf"    def {govde_adi}\(self\):.*?(?=\n    def )", gui, _re.S)
            self.assertIsNotNone(m, govde_adi)
            self.assertIn(kontrolor, m.group(0), govde_adi)
            for ham in ("sync_manual_collections(", "scan_and_relink_files(", "reconcile_download_folders(", "bilinmeyenleri_tasi("):
                self.assertNotIn(ham, m.group(0), f"{govde_adi}: kontrolor yerine ham fonksiyon cagiriyor ({ham})")
        cli = (HERE / "yt-arsiv.py").read_text(encoding="utf-8")
        self.assertNotIn("os.replace", cli)
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count('"moved_to_bilinmeyen"'), 1, "bilinmeyen tasima TEK kapidan")
        self.assertEqual(core.count("BILINMEYEN_DIR = "), 1)

    def test_youtube_yoklamasi_tek_kapidan(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            for yasak in ("_video_durumu", "INTO youtube_durum", "FROM youtube_durum", "yt_dlp.YoutubeDL"):
                self.assertNotIn(yasak, src, f"{ad}: YouTube yoklama kapisini atliyor ({yasak})")
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("INSERT OR REPLACE INTO youtube_durum"), 1, "youtube_durum'a yazma tek kapidan")


    def test_eslestirme_tek_kapidan(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            for yasak in ("os.link", "os.replace", "FROM kopya_farkli", "INTO kopya_farkli", "kopyalari_esle",
                          "_bagla(", "baglanti.tmp"):
                self.assertNotIn(yasak, src, f"{ad}: eslestirme kapisini atliyor ({yasak})")
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("_os.link("), 2, "sabit bag yalniz _ShutilBag.copy2 ve _bagla'dan kurulur")
        self.assertEqual(core.count("_os.replace("), 1, "dosya degistirme yalniz _bagla'da")
        self.assertEqual(core.count("DELETE FROM kopya_farkli"), 1, "tabloya yazma tek kapidan")
        self.assertEqual(len(re.findall(r"INSERT INTO kopya_farkli", core)), 1)

    def test_ses_analizi_tek_kapidan(self):
        for ad in ("YT-Arsiv-GUI.pyw", "yt-arsiv.py"):
            src = (HERE / ad).read_text(encoding="utf-8")
            for yasak in ("fpcalc", "ses_izi", "ses_cift", "ses_iz ", "ses_eslesme", "_fpcalc_iz", "_ses_iz_yaz", "_ses_cift_yaz"):
                self.assertNotIn(yasak, src, f"{ad}: ses kapisini atliyor ({yasak})")
        gui = (HERE / "YT-Arsiv-GUI.pyw").read_text(encoding="utf-8")
        self.assertIn("core.ses_analizi", gui)
        self.assertIn("core.ses_etiketleri", gui)
        core = (HERE / "ytarsiv_core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("str(SES_ARACI)"), 1, "fpcalc'i calistiran TEK yer _fpcalc_iz olmali")
        self.assertEqual(len(re.findall(r"INSERT OR REPLACE INTO ses_", core)), 2, "ses tablolarina yazma 2 kapidan")
        self.assertNotIn("DELETE FROM ses_", core)

    def test_gui_inceleme_yok_ve_silme_tek_kapidan(self):
        src = (HERE / "YT-Arsiv-GUI.pyw").read_text(encoding="utf-8")
        self.assertNotIn("inceleme", src.lower(), "GUI'de Inceleme kalintisi var")
        self.assertNotIn("DELETE FROM videos", src, "GUI kaydi kapiyi atlayarak siliyor")
        self.assertNotIn("_silinenler", src, "GUI'de eski klasor adi kalmis (artik _ListeDisi)")
        self.assertNotIn("Kayip Videolar", src, "sekme artik 'Liste Disi'")
        self.assertIn('text="Liste Disi"', src)
        self.assertIn('text="Yayindan Kalkan"', src)
        self.assertIn("core.yayindan_kalkanlar", src, "Yayindan Kalkan sekmesi okuma kapisini kullanmiyor")
        # Ayni Videolar silme isleyicisi YALNIZ kopya_kaldir kapisini kullanir (Kayip sekmesi ayri is).
        a = src.index("    def on_dup_delete_selected_files(self):")
        b = src.index("    def on_dup_copy_title(self):")
        govde = src[a:b]
        self.assertIn("core.kopya_kaldir", govde)
        self.assertIsNone(re.search(r"\bpermanently_delete_video\s*\(|\bUPDATE videos\b", govde),
                          "Ayni Videolar silme kopya_kaldir kapisini atliyor")


if __name__ == "__main__":
    unittest.main()
