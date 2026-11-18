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
- **Depolama:** birincil depo dolarsa yeni indirmeler ek depoya (başka sürücü/klasör) gider; hiçbir yerde yer yoksa indirme temiz durur
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

Arayüz işleve göre düzenlidir:

- **Araç çubuğu:** `+ Playlist / Kanal Ekle` · `Tümünü Tara` / `Seçiliyi Tara` · `Bakım ve Temizlik` · `Ayarlar` · video arama.
  Onay bekleyen toplu silinme varsa ayrıca `Onay bekleyen: N` düğmesi görünür.
- **Sekmeler:** `Playlistler`, `Arama` (arşiv) · `Liste Dışı`, `Yayından Kalkan`, `Aynı Videolar` (temizlik; başlıkta
  kayıt sayısı görünür) · `Geçmiş`. Sütun başlığına tıklayınca tablo o sütuna göre sıralanır.
- **Bakım ve Temizlik menüsü:** silinme onayları, klasör onarımı (kayıp dosyaları yeniden bağlama), elle eklenen klasörlerin
  taranması, kopya temizliği / ses analizi, yayından kalkanların YouTube'da yeniden kontrolü.
- **Ayarlar:** `Depolama` (depolar, boş yer sınırı), `İndirme` (indirmeler arası bekleme, uzun videolarda kalite
  sınırı), `Bilgi` (klasörler, yt-dlp / ffmpeg durumu, çerez dosyası).
- Arka planda tarama/indirme sürerken silme ve ayar değiştiren işlemler başlatılamaz; pencere kapatılırken sorulur.
- Günlük ekrandaki panelde ve `logs\gui-YYYYAAGG.log` dosyasında tutulur.
- Kısayollar: `Ctrl+N` ekle, `Ctrl+T` tümünü tara, `Ctrl+F` arama, `Ctrl+,` ayarlar, `F5` yenile, `Esc` iptal.

Komut satırı:

```
python yt-arsiv.py add https://www.youtube.com/playlist?list=...  --name Euro1
python yt-arsiv.py add https://www.youtube.com/@kanal
python yt-arsiv.py list
python yt-arsiv.py sync               # tara + yenileri indir + kayıpları işaretle
python yt-arsiv.py status             # kayıp / sorunlu videolar
python yt-arsiv.py silinme            # onay bekleyen toplu silinmeler (--onayla / --koru)
python yt-arsiv.py tara --kuru        # veritabanıyla bağsız dosyaları listele
python yt-arsiv.py depo               # depoların boş yeri ve kayıt sayısı
python yt-arsiv.py depo --ekle D:/YT-Arsiv-2    # ek depo ekle
python yt-arsiv.py ayar               # indirme ayarları + klasörler + yt-dlp/ffmpeg durumu
python yt-arsiv.py ayar --bekleme 20 40 --uzun-dk 30 --uzun-p 720
python yt-arsiv.py history --limit 50
```

## İndirme ayarları

- **İndirmeler arası bekleme** (varsayılan 30-60 sn): art arda indirmeler arasında bu aralıkta rastgele beklenir.
  Kısaltmak taramayı hızlandırır ama YouTube'un bot engeline takılma riskini artırır.
- **Uzun videolarda kalite sınırı** (varsayılan: 20 dakikadan uzun videolar en fazla 1080p): DJ seti gibi uzun
  videoların diski doldurmaması içindir. `0` / "Sınır yok" en iyi kaliteyi indirir.
- Ayar dosyası (`config.json`) bozulursa uygulama üzerine yazmaz ve uyarır; dosyayı düzeltin ya da silin.

## Depolama (disk dolarsa)

Arayüzde **Ayarlar > Depolama** (komut satırında `depo`) birincil depoyu (`downloads`) ve ek depoları gösterir.

- Bir depoda **en az 2048 MB** boş yer kalmalıdır (ayarlanabilir); altına düşerse yeni indirme o depoya başlamaz.
- Birincil depo dolarsa yeni indirmeler sırayla **ek depolara** gider; kayıt dosyanın tam yolunu tutar.
- Hiçbir depoda yer yoksa indirme **temiz durur**: tek uyarı yazılır, "indirme hatası" kaydı oluşmaz,
  yer açılınca ya da ek depo eklenince bir sonraki tarama kaldığı yerden devam eder.
- Dosyalar bulundukları depoda kalır; `_ListeDisi` arşivi de aynı depoda tutulur. Mevcut dosyalar taşınmaz.
- Ek depolar `config.json` içindeki `ek_depolar` listesinde durur (arayüz/`depo` komutu yazar).
- Sınırlar: sabit bağ (yer kazandıran birleştirme) yalnız aynı sürücüde çalışır; ek depodaki dosyaların
  numarası indirildiği andaki sıraya göre kalır (sonradan yeniden numaralanmaz).

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
