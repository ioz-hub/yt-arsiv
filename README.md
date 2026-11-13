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

## Kurulum

### Hazır sürüm (önerilen)

[Releases](../../releases) sayfasından `YT-Arsiv-win64.zip` dosyasını indirin, yazılabilir bir klasöre çıkarın
(Program Files dışında) ve `YT-Arsiv.exe`'yi çalıştırın. Python, yt-dlp, ffmpeg ya da fpcalc kurmanız gerekmez;
hepsi pakette gelir. Komut satırı sürümü: `yt-arsiv-cli.exe`.

- Veritabanı (`videos.db`), `downloads\` klasörü ve isteğe bağlı `cookies.txt` exe'nin yanında durur.
- Exe imzasızdır; Windows SmartScreen uyarırsa "Ek bilgi → Yine de çalıştır" deyin. Bazı antivirüsler
  PyInstaller paketlerine yanlış alarm verebilir.
- Güncelleme: yeni zip'i çıkarırken `videos.db` ve `downloads\` klasörünüze dokunmayın.

### Kaynaktan çalıştırma

- Windows, **Python 3.14** (çekirdek `ytarsiv_core_cached.pyc` Python 3.14 bayt kodudur; başka sürümde çalışmaz)
- `pip install yt-dlp`, ayrıca `ffmpeg` (PATH'te ya da `araclar\` içinde)
- Ses parmak izi için `araclar\fpcalc.exe` (Chromaprint 1.5.1) depoda hazır gelir
  (kaynak ve sürüm: `araclar\KAYNAK.txt`, lisans notu: `THIRD-PARTY.md`)
- Exe derlemek için: `pip install pyinstaller`, sonra `python derle.py --ffmpeg C:\yol\ffmpeg.exe`

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
