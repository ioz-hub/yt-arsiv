"""YT-Arsiv CLI - komut satiri arayuzu.

GUI icin: YT-Arsiv-GUI.pyw veya Baslat.bat
"""

import argparse
import sys
from pathlib import Path

# Baslik/log satirlarindaki ♫ gibi karakterler cp1254 konsolda/dosya yonlendirmesinde tarama ortasinda
# cokertmesin: yazilamayan karakter '?' olur.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from ytarsiv_core import (
    add_playlist,
    kanal_mi,
    bilinmeyenleri_tasi,
    depo_durumu,
    depo_ekle,
    depo_kaldir,
    depo_min_bos_ayarla,
    depo_min_bos_mb,
    indirme_ayarlari,
    indirme_ayarlari_yaz,
    ortam_bilgisi,
    SURUM,
    kanal_url_duzenle,
    manuel_tarama,
    tum_klasorleri_tara,
    delete_playlist,
    get_events,
    get_lost_videos,
    init_db,
    list_playlists,
    resolve_video_path,
    silinme_bekleyenler,
    silinmeyi_koru,
    silinmeyi_onayla,
    sync_two_phase,
)


def cmd_add(args, conn):
    url = kanal_url_duzenle(args.url)
    pid, err = add_playlist(conn, args.url, args.name or "")
    if err:
        print(err)
    else:
        print(f"Eklendi [id={pid}]: {url}" + ("  (kanal: tum yuklemeler taranir/indirilir)" if kanal_mi(args.url) else ""))
        if args.name:
            print(f"  Takma ad: {args.name}")
        print("Indirmek icin:  python yt-arsiv.py sync")


def cmd_list(args, conn):
    rows = list_playlists(conn)
    if not rows:
        print("Kayitli playlist yok. 'add' komutu ile ekle.")
        return
    for r in rows:
        print(f"[{r['id']}] {r['alias'] or r['title'] or '(isimsiz)'}")
        print(f"    URL: {r['url']}")
        print(f"    Aktif: {r['active']}  Indirilen: {r['downloaded']}  "
              f"Yayindan kalkan: {r['unavailable']}  Liste disi: {r['removed']}")
        print(f"    Son tarama: {r['last_checked'] or '-'}")


def cmd_remove(args, conn):
    delete_playlist(conn, args.id, delete_files=args.delete_files)
    print(f"Playlist [{args.id}] silindi.")


def cmd_sync(args, conn):
    summary = sync_two_phase(conn, playlist_id=args.playlist, log=print)
    print(f"\n=== OZET ===")
    print(f"Taranan playlist      : {summary['playlists']}")
    print(f"Yeni indirilen        : {summary['new']}")
    print(f"Listeden cikarilan    : {summary['removed']}")
    print(f"Yayindan kalkan       : {summary['unavailable']}")
    print(f"_YayındanKalkan'a tas.: {summary.get('yayindan_kalkan_tasinan', 0)}")
    print(f"Sabit bagla birlesen  : {summary.get('kopya_baglanan', 0)} dosya ({summary.get('kopya_kazanilan_mb', 0):.0f} MB)")
    print(f"Farkli kopya (kayitli): {summary.get('kopya_farkli', 0)}")
    print(f"Hata                  : {summary['errors']}")
    if summary['removed'] or summary['unavailable']:
        print("\nDetay icin:  python yt-arsiv.py status")
    if summary.get('depo_yonlendirilen'):
        print(f"Ek depoya giden       : {summary['depo_yonlendirilen']} indirme (birincil depo dolu)")
    if summary.get('depo_dolu'):
        print(f"\n[!] DEPO DOLU: {summary['depo_dolu']} video indirilemedi (sonraki taramada yeniden denenir).")
        print("    python yt-arsiv.py depo                   (bos yeri goster)")
        print("    python yt-arsiv.py depo --ekle D:/Arsiv2  (ek depo ekle)")
    if summary.get('silinme_onay_bekleyen'):
        print(f"\n[?] {summary['silinme_onay_bekleyen']} video listede gorunmuyor ama silinmis sayilmadi (onayin bekleniyor):")
        print("    python yt-arsiv.py silinme            (listele)")
        print("    python yt-arsiv.py silinme --onayla   (hepsi listeden cikmis sayilsin)")
        print("    python yt-arsiv.py silinme --koru     (hicbiri silinmedi, kalsin)")


def cmd_tara(args, conn):
    if args.kuru:
        o = bilinmeyenleri_tasi(conn, kuru=True)
        print(f"Bagsiz: {len(o['dosyalar'])} dosya  (baglanabilir atlanan: {o['atlanan_baglanabilir']}, gecici: {o['atlanan_gecici']})")
        for yol, tur in o["dosyalar"][:200]:
            print(f"  [{tur}] {resolve_video_path(str(yol)) or yol}")
        if o["durdu"]:
            print("[!] " + o["durdu"])
        return
    if args.tum:
        s = tum_klasorleri_tara(conn, log=print)
        bil = s["bilinmeyen"]
        print(f"\nTaranan: {s['scanned_files']}  Yeniden baglanan: {s['relinked']}  Hala kayip: {s['no_match']}")
    else:
        s = manuel_tarama(conn, log=print)
        bil = s["bilinmeyen"]
        print(f"\nKoleksiyon: {s['collections']}  Yeni dosya: {s['new_files']}")
    print(f"_Bilinmeyen'e tasinan: {bil['tasinan_video']} video + {bil['tasinan_yan']} yan dosya")
    if bil.get("durdu"):
        print("[!] DURDU: " + bil["durdu"])


def cmd_silinme(args, conn):
    bek = silinme_bekleyenler(conn)
    if not bek:
        print("Onay bekleyen silinme yok.")
        return
    for b in bek:
        print(f"[{b['playlist_alias']}] {b['title'] or '(baslik yok)'}  ({b['video_id']})  dosya: {'VAR' if b['dosya_var'] else 'YOK'}")
    anahtarlar = [(b["playlist_id"], b["video_id"]) for b in bek]
    if args.onayla and args.koru:
        print("--onayla ve --koru birlikte verilemez.")
    elif args.onayla:
        print(f"{silinmeyi_onayla(conn, anahtarlar)} video listeden cikmis sayildi.")
    elif args.koru:
        print(f"{silinmeyi_koru(conn, anahtarlar)} video korundu (silinmedi).")
    else:
        print(f"\n{len(bek)} video onay bekliyor.  --onayla (hepsini silinmis say) ya da --koru (hepsi kalsin).")


def cmd_depo(args, conn):
    if args.ekle:
        ok, sonuc = depo_ekle(args.ekle)
        print(f"Ek depo eklendi: {sonuc}" if ok else f"Eklenemedi: {sonuc}")
    if args.kaldir:
        ok, sonuc = depo_kaldir(conn, args.kaldir)
        print(f"Ek depo kaldirildi ({sonuc} video kaydi orada duruyor, dosyalar yerinde)." if ok else f"Kaldirilamadi: {sonuc}")
    if args.min_bos is not None:
        ok, sonuc = depo_min_bos_ayarla(args.min_bos)
        print(f"Minimum bos yer: {sonuc} MB" if ok else f"Ayarlanamadi: {sonuc}")
    print(f"Minimum bos yer: {depo_min_bos_mb()} MB (bir depoda bundan az yer kalirsa yeni indirme o depoya baslamaz)")
    for d in depo_durumu(conn):
        tur = "birincil" if d["ana"] else "ek"
        print(f"[{tur:8s}] {d['yol']}")
        print(f"           bos {d['bos'] / 2 ** 30:.1f} GB / toplam {d['toplam'] / 2 ** 30:.1f} GB   video kaydi: {d['kayit']}"
              + ("" if d["var"] else "   [TAKILI DEGIL]"))


def cmd_ayar(args, conn):
    ay = indirme_ayarlari()
    if args.bekleme or args.uzun_dk is not None or args.uzun_p is not None:
        a, b = args.bekleme or ay["bekleme"]
        ok, sonuc = indirme_ayarlari_yaz(a, b, ay["uzun_dk"] if args.uzun_dk is None else args.uzun_dk,
                                         ay["uzun_p"] if args.uzun_p is None else args.uzun_p)
        if not ok:
            print(f"Ayarlanamadi: {sonuc}")
            return
        ay = sonuc
    print(f"Indirmeler arasi bekleme : {ay['bekleme'][0]:g} - {ay['bekleme'][1]:g} sn")
    print(f"Uzun video kalite siniri : {ay['uzun_dk']} dk'dan uzun videolar en fazla "
          + ("sinirsiz (en iyi kalite)" if not ay["uzun_p"] else f"{ay['uzun_p']}p"))
    b = ortam_bilgisi()
    print(f"Surum                    : {b['surum']}")
    print(f"Arsiv klasoru            : {b['arsiv']}")
    print(f"Veritabani               : {b['veritabani']}")
    print(f"Ayar dosyasi             : {b['ayar_dosyasi']}" + ("   [BOZUK]" if b["ayar_dosyasi_bozuk"] else ""))
    print(f"yt-dlp                   : {b['yt_dlp'] or 'YOK'}")
    print(f"ffmpeg                   : {b['ffmpeg'] or 'YOK'}")
    print(f"Cerez dosyasi            : {b['cerez'] or 'yok'}")


def cmd_status(args, conn):
    rows = get_lost_videos(conn)
    if not rows:
        print("Tum videolar aktif. Kayip/sorunlu video yok.")
        return
    print(f"\n{len(rows)} kayip/sorunlu video:\n")
    for r in rows:
        plist = r['playlist_alias'] or r['playlist_title'] or f"playlist_{r['playlist_id']}"
        fpath = r['file_path']
        abs_p = resolve_video_path(fpath)
        if abs_p and abs_p.exists():
            file_info = f"[OK] Yerel: {abs_p}"
        elif fpath:
            file_info = f"[!!] DB'de var ama dosya yok: {fpath}"
        else:
            file_info = "[XX] YEREL KOPYA YOK - kurtarilamaz"
        status_tr = {
            "removed_from_playlist": "Listeden cikarilmis (sen yaptin)",
            "unavailable":           "YouTube'da erisilemiyor (YouTube yapti)",
        }.get(r['status'], r['status'])
        print(f"--- [{status_tr}] {r['title'] or '(baslik yok)'}")
        print(f"    Video ID : {r['video_id']}   (https://youtu.be/{r['video_id']})")
        print(f"    Playlist : [{r['playlist_id']}] {plist}")
        print(f"    Uploader : {r['uploader'] or '?'}")
        print(f"    Son gor. : {r['last_seen']}")
        print(f"    {file_info}")
        print()


def cmd_history(args, conn):
    rows = get_events(conn, args.limit)
    if not rows:
        print("Henuz olay yok.")
        return
    for r in rows:
        plist = r['playlist_alias'] or r['playlist_title'] or "-"
        d = (r['details'] or "")[:80]
        print(f"{r['timestamp']}  {r['event_type']:24s}  {r['video_id'] or '-':12s}  [{plist}]  {d}")


def main():
    parser = argparse.ArgumentParser(description="YT-Arsiv CLI")
    parser.add_argument("--version", action="version", version=f"YT-Arsiv {SURUM}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="Yeni playlist veya kanal ekle (URL: playlist ya da https://www.youtube.com/@kanal)")
    p.add_argument("url")
    p.add_argument("--name", help="Klasor adi / takma ad")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="Kayitli playlistleri goster")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="Playlist sil")
    p.add_argument("id", type=int)
    p.add_argument("--delete-files", action="store_true", help="Inen dosyalari da sil")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("sync", help="Tara + yenileri indir + kayiplari isaretle")
    p.add_argument("--playlist", type=int, help="Sadece bu playlist id'sini isle")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("tara", help="Yerel klasor taramasi: varsayilan manuel ('_' klasorleri); --tum tum klasorler; --kuru yalniz bagsiz dosyalari listele")
    p.add_argument("--tum", action="store_true", help="Tum klasorleri tara (kayiplari yeniden bagla + klasor uzlastirma)")
    p.add_argument("--kuru", action="store_true", help="Hicbir sey tasima, bagsiz dosyalari listele")
    p.set_defaults(func=cmd_tara)

    p = sub.add_parser("silinme", help="Cok sayida silinme tespitinde onay bekleyen videolar (listele / --onayla / --koru)")
    p.add_argument("--onayla", action="store_true", help="Hepsi listeden cikmis sayilsin")
    p.add_argument("--koru", action="store_true", help="Hicbiri silinmedi, kalsin")
    p.set_defaults(func=cmd_silinme)

    p = sub.add_parser("depo", help="Depolama: bos yer + ek depolar (--ekle YOL / --kaldir YOL / --min-bos MB)")
    p.add_argument("--ekle", help="Ek depo klasoru ekle (birincil dolunca yeni indirmeler oraya gider)")
    p.add_argument("--kaldir", help="Ek depoyu listeden kaldir (dosyalar silinmez)")
    p.add_argument("--min-bos", type=int, help="Bir depoda en az bu kadar bos yer (MB) kalmali")
    p.set_defaults(func=cmd_depo)

    p = sub.add_parser("ayar", help="Indirme ayarlari + ortam bilgisi (--bekleme EN_AZ EN_COK / --uzun-dk N / --uzun-p N)")
    p.add_argument("--bekleme", nargs=2, type=float, metavar=("EN_AZ", "EN_COK"), help="Indirmeler arasi bekleme (saniye)")
    p.add_argument("--uzun-dk", type=int, help="Bu kadar dakikadan uzun videolara kalite siniri uygulanir")
    p.add_argument("--uzun-p", type=int, help="Uzun videolar icin en yuksek cozunurluk (0 = sinir yok, 480/720/1080/1440/2160)")
    p.set_defaults(func=cmd_ayar)

    p = sub.add_parser("status", help="Kayip/sorunlu videolari listele")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("history", help="Olay gecmisini goster")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_history)

    args = parser.parse_args()
    conn = init_db()
    try:
        args.func(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
