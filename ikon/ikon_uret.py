"""YT-Arsiv simgesini uretir: ikon\\yt-arsiv.ico (16-256 px) + ikon\\yt-arsiv.png (onizleme).

Tasarim: YATAY USB FLAS BELLEK.  Solda YouTube kirmizisi govde ve ortasinda beyaz OYNAT isareti; saga bakan metal USB-A basligi.
Oynat isareti ile USB basligi AYNI YONE (saga) bakar: "YouTube'dan oynat -> belleğe/diske kaydet".
Calistirma:  python ikon\\ikon_uret.py   (Pillow gerekir; yalniz simge degisecekse)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

KLASOR = Path(__file__).resolve().parent
S = 1024                          # cizim boyutu (sonra kuculur: kenar yumusatma)
KIRMIZI = (255, 0, 0, 255)        # YouTube kirmizisi
KIRMIZI_KOYU = (190, 0, 0, 255)
KIRMIZI_ACIK = (255, 80, 80, 255)
BEYAZ = (255, 255, 255, 255)
METAL = (214, 218, 224, 255)      # USB basligi (gumus)
METAL_KOYU = (150, 156, 166, 255)
METAL_ACIK = (246, 248, 250, 255)
DELIK = (60, 64, 72, 255)
GOLGE = (0, 0, 0, 70)


def ciz(ayrinti=True):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cy = S // 2
    # --- olculer ---
    g_sol, g_sag = 50, 720                                   # govde x araligi
    g_h = 540                                                # govde yuksekligi
    g_ust, g_alt = cy - g_h // 2, cy + g_h // 2
    r = 130
    b_sol, b_sag = g_sag - 40, S - 46                        # baslik x araligi (govdeye biner)
    b_h = 360
    b_ust, b_alt = cy - b_h // 2, cy + b_h // 2

    # golge
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(g).rounded_rectangle((g_sol + 12, g_ust + 24, b_sag + 12, g_alt + 24), radius=r, fill=GOLGE)
    im.alpha_composite(g.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(im)

    # --- USB-A basligi (metal, saga bakar) --- govdenin ARKASINDA cizilir (govde onu kismen kapatir)
    d.rectangle((b_sol, b_ust, b_sag, b_alt), fill=METAL)
    d.rectangle((b_sol, b_ust, b_sag, b_alt), outline=METAL_KOYU, width=8)
    d.rectangle((b_sol + 14, b_ust + 18, b_sag - 14, b_ust + 38), fill=METAL_ACIK)           # ust parlak serit
    # iki baglanti deligi (alt alta: baslik saga baktigi icin)
    dh, dw = 62, 100
    dx = b_sol + 120
    if ayrinti:
        d.rounded_rectangle((dx, cy - 86 - dh // 2, dx + dw, cy - 86 + dh // 2), radius=10, fill=DELIK)
        d.rounded_rectangle((dx, cy + 86 - dh // 2, dx + dw, cy + 86 + dh // 2), radius=10, fill=DELIK)
    else:
        d.rectangle((dx - 6, cy - 120, dx + dw + 20, cy - 40), fill=DELIK)
        d.rectangle((dx - 6, cy + 40, dx + dw + 20, cy + 120), fill=DELIK)
    # baslik - govde birlesim halkasi
    d.rectangle((b_sol - 6, b_ust - 22, b_sol + 34, b_alt + 22), fill=METAL_KOYU)

    # --- govde (YouTube kirmizisi) ---
    d.rounded_rectangle((g_sol, g_ust, g_sag, g_alt), radius=r, fill=KIRMIZI)
    d.rounded_rectangle((g_sol, g_ust, g_sag, g_alt), radius=r, outline=KIRMIZI_KOYU, width=8)
    if ayrinti:
        d.rounded_rectangle((g_sol + 70, g_ust + 24, g_sag - 70, g_ust + 44), radius=10, fill=KIRMIZI_ACIK)   # ust isik yansimasi

    # --- oynat isareti: govdenin ortasinda, SAGA bakar (USB basligi ile ayni yon) ---
    px, py = (g_sol + g_sag) // 2 + 6, cy
    h = 175
    d.polygon([(px - h * 0.62, py - h), (px - h * 0.62, py + h), (px + h * 1.02, py)], fill=BEYAZ)
    # anahtarlik deligi (solda)
    if ayrinti:
        d.ellipse((g_sol + 52, cy - 34, g_sol + 52 + 68, cy + 34), fill=(0, 0, 0, 0), outline=KIRMIZI_KOYU, width=9)
    return im


def kanvasi_doldur(im, kenar=0.04):
    """Cizimin saydam kenar bosluklarini kirpar ve kareyi dolduracak sekilde ortalar (yatay sekil kucuk boyutta daha buyuk gorunur)."""
    kutu = im.getbbox()
    kes = im.crop(kutu)
    boy = int(max(kes.size) * (1 + 2 * kenar))
    kare = Image.new("RGBA", (boy, boy), (0, 0, 0, 0))
    kare.paste(kes, ((boy - kes.size[0]) // 2, (boy - kes.size[1]) // 2))
    return kare


def main():
    boyutlar = [256, 128, 64, 48, 32, 24, 16]
    buyuk, kucuk = kanvasi_doldur(ciz(True)), kanvasi_doldur(ciz(False))
    resimler = [(buyuk if b >= 48 else kucuk).resize((b, b), Image.LANCZOS) for b in boyutlar]
    resimler[0].save(KLASOR / "yt-arsiv.ico", format="ICO", sizes=[(b, b) for b in boyutlar], append_images=resimler[1:])
    buyuk.resize((512, 512), Image.LANCZOS).save(KLASOR / "yt-arsiv.png")
    print("yazildi:", KLASOR / "yt-arsiv.ico", [r.size for r in resimler])


if __name__ == "__main__":
    main()
