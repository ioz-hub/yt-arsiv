"""YT-Arsiv simgesini uretir: ikon\\yt-arsiv.ico (16-256 px) + ikon\\yt-arsiv.png (onizleme).

Tasarim: YATAY USB FLAS BELLEK.  Solda YouTube kirmizisi govde ve ortasinda beyaz OYNAT isareti; saga bakan metal USB-A basligi.
Oynat isareti ile USB basligi AYNI YONE (saga) bakar: "YouTube'dan oynat -> bellege/diske kaydet".
Iki cizim: BUYUK (>=48 px: ayrintili) ve KUCUK (gorev cubugu 16-40 px: govde kareye yakin, kalin sekiller, yuksek kontrast).
Calistirma:  python ikon\\ikon_uret.py   (Pillow gerekir; yalniz simge degisecekse)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

KLASOR = Path(__file__).resolve().parent
S = 1024                          # cizim boyutu (sonra kuculur: kenar yumusatma)
KIRMIZI = (255, 0, 0, 255)        # YouTube kirmizisi
KIRMIZI_KOYU = (180, 0, 0, 255)
KIRMIZI_ACIK = (255, 90, 90, 255)
BEYAZ = (255, 255, 255, 255)
METAL = (222, 226, 232, 255)      # USB basligi (gumus)
METAL_KOYU = (140, 146, 156, 255)
METAL_ACIK = (250, 251, 252, 255)
DELIK = (50, 54, 62, 255)
GOLGE = (0, 0, 0, 70)


def ciz(kucuk=False):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cy = S // 2
    # --- olculer: kucukte govde daha yuksek, baslik daha kisa (kareyi doldurur) ---
    g_sol, g_sag = 40, 700 if kucuk else 705
    g_h = 820 if kucuk else 720
    g_ust, g_alt = cy - g_h // 2, cy + g_h // 2
    r = 170 if kucuk else 150
    b_sol, b_sag = g_sag - 40, S - 40
    b_h = 440 if kucuk else 400
    b_ust, b_alt = cy - b_h // 2, cy + b_h // 2

    if not kucuk:                                           # golge (yalniz buyuk boyutta)
        g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle((g_sol + 12, g_ust + 24, b_sag + 12, g_alt + 24), radius=r, fill=GOLGE)
        im.alpha_composite(g.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(im)

    # --- USB-A basligi (metal, saga bakar): govdenin ARKASINDA ---
    d.rectangle((b_sol, b_ust, b_sag, b_alt), fill=METAL)
    d.rectangle((b_sol, b_ust, b_sag, b_alt), outline=METAL_KOYU, width=14 if kucuk else 8)
    if not kucuk:
        d.rectangle((b_sol + 14, b_ust + 18, b_sag - 14, b_ust + 38), fill=METAL_ACIK)
    # iki baglanti deligi (alt alta)
    dh, dw = (96, 150) if kucuk else (66, 100)
    dx = b_sol + (130 if kucuk else 120)
    ofs = 105 if kucuk else 92
    d.rounded_rectangle((dx, cy - ofs - dh // 2, dx + dw, cy - ofs + dh // 2), radius=12, fill=DELIK)
    d.rounded_rectangle((dx, cy + ofs - dh // 2, dx + dw, cy + ofs + dh // 2), radius=12, fill=DELIK)
    d.rectangle((b_sol - 6, b_ust - 22, b_sol + 36, b_alt + 22), fill=METAL_KOYU)           # birlesim halkasi

    # --- govde (YouTube kirmizisi) ---
    d.rounded_rectangle((g_sol, g_ust, g_sag, g_alt), radius=r, fill=KIRMIZI)
    d.rounded_rectangle((g_sol, g_ust, g_sag, g_alt), radius=r, outline=KIRMIZI_KOYU, width=14 if kucuk else 8)
    if not kucuk:
        d.rounded_rectangle((g_sol + 70, g_ust + 24, g_sag - 70, g_ust + 44), radius=10, fill=KIRMIZI_ACIK)

    # --- oynat isareti: govdenin ortasinda, SAGA bakar ---
    px, py = (g_sol + g_sag) // 2 + (14 if kucuk else 6), cy
    h = 290 if kucuk else 195
    d.polygon([(px - h * 0.62, py - h), (px - h * 0.62, py + h), (px + h * 1.02, py)], fill=BEYAZ)
    if not kucuk:                                           # anahtarlik deligi
        d.ellipse((g_sol + 52, cy - 34, g_sol + 52 + 68, cy + 34), fill=(0, 0, 0, 0), outline=KIRMIZI_KOYU, width=9)
    return im


def kanvasi_doldur(im, kenar=0.02):
    """Cizimin saydam kenar bosluklarini kirpar ve kareyi dolduracak sekilde ortalar."""
    kes = im.crop(im.getbbox())
    boy = int(max(kes.size) * (1 + 2 * kenar))
    kare = Image.new("RGBA", (boy, boy), (0, 0, 0, 0))
    kare.paste(kes, ((boy - kes.size[0]) // 2, (boy - kes.size[1]) // 2))
    return kare


def main():
    boyutlar = [256, 128, 64, 48, 40, 32, 24, 20, 16]
    buyuk, kucuk = kanvasi_doldur(ciz(False)), kanvasi_doldur(ciz(True))
    resimler = []
    for b in boyutlar:
        kaynak = buyuk if b >= 64 else kucuk
        r = kaynak.resize((b * 4, b * 4), Image.LANCZOS).resize((b, b), Image.LANCZOS) if b < 64 else kaynak.resize((b, b), Image.LANCZOS)
        resimler.append(r)
    resimler[0].save(KLASOR / "yt-arsiv.ico", format="ICO", sizes=[(b, b) for b in boyutlar], append_images=resimler[1:])
    buyuk.resize((512, 512), Image.LANCZOS).save(KLASOR / "yt-arsiv.png")
    print("yazildi:", KLASOR / "yt-arsiv.ico", [r.size for r in resimler])


if __name__ == "__main__":
    main()
