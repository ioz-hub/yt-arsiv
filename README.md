# YT-Arsiv

YouTube oynatma listelerini (ve kanalları) Windows'ta yerel diske arşivleyen küçük bir araç.
Listedeki videoları `yt-dlp` ile indirir; listeden çıkarılan, gizlenen ya da silinen videoları
tespit edip ayrı izler, böylece arşivinizde hiçbir şey sessizce kaybolmaz.

- Playlist **ve kanal** ekleme (kanalda tüm yüklemeler taranır)
- Yeni videoları indirir; indirilen dosyaların sırasını ve adını düzenli tutar
- Listeden çıkanlar / YouTube'da yayından kalkanlar için ayrı sekmeler (`_ListeDisi`, `_YayındanKalkan`)
- `downloads` altındaki `_` ile başlayan her klasör otomatik bir "koleksiyon" sekmesi olur
- Veritabanıyla bağı olmayan dosyaları `_Bilinmeyen` klasörüne toplar
- "Aynı Videolar": farklı listelerdeki aynı videoları başlık, süre ve (isteğe bağlı) ses parmak iziyle bulur
- Çok sayıda videonun listeden kaybolduğu durumda kendi başına karar vermez, size sorar
- tkinter arayüzü (`YT-Arsiv-GUI.pyw`) ve komut satırı (`yt-arsiv.py`)

> Bu depo yalnızca uygulamayı içerir. Kişisel playlist listeleri, veritabanı, çerezler ve indirilen
> dosyalar depoya **girmez**; ilk çalıştırmada boş bir veritabanı oluşur.

## Gereksinimler

- Windows, **Python 3.14** (çekirdek `ytarsiv_core_cached.pyc` olarak Python 3.14 bayt kodudur; başka sürümde çalışmaz)
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (PATH'te olmalı) ve `ffmpeg`
- Ses parmak izi (Aynı Videolar'da ses analizi) için `fpcalc.exe` (Chromaprint 1.5.1) gerekir; depoda
  `araclar\fpcalc.exe` olarak hazır gelir, ayrıca bir şey kurmanız gerekmez
  (kaynak ve sürüm bilgisi: `araclar\KAYNAK.txt`, lisans notu: `THIRD-PARTY.md`)

## Kullanım

Arayüz:

```
Baslat.bat
```

Komut satırı:

```
python yt-arsiv.py add https://www.youtube.com/playlist?list=...  --name Euro1
python yt-arsiv.py add https://www.youtube.com/@kanal
python yt-arsiv.py list
python yt-arsiv.py sync               # tara + yenileri indir + kayıpları işaretle
python yt-arsiv.py status             # kayıp / sorunlu videolar
python yt-arsiv.py silinme            # onay bekleyen toplu silinmeler (--onayla / --koru)
python yt-arsiv.py tara --kuru        # veritabanıyla bağsız dosyaları listele
python yt-arsiv.py history --limit 50
```

Bot kontrolüne takılırsanız tarayıcınızdan dışa aktardığınız `cookies.txt` dosyasını uygulama
klasörüne koyun (depoya girmez).

## Testler

```
python -m unittest test_kapi
```

## Notlar

- `ytarsiv_core.py` çekirdeğin üstüne oturan ince bir kabuktur; çekirdek mantığın bir bölümü
  derlenmiş `ytarsiv_core_cached.pyc` içindedir (kaynağı yoktur).
- Tüm yazma/silme işlemleri tek kapılardan geçer; kapıyı atlayan kodu `test_kapi.py` yakalar.
- Silme işlemleri kalıcıdır (dosyalar diskten silinir). Önemli arşivde önce `videos.db` yedeği alın.
