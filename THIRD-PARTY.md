# Üçüncü taraf bileşenler

## araclar/fpcalc.exe — Chromaprint 1.5.1

Ses parmak izi çıkarmak için kullanılan, [AcoustID / Chromaprint](https://github.com/acoustid/chromaprint)
projesinin resmi Windows derlemesidir; **değiştirilmeden** olduğu gibi dağıtılır.

- Kaynak kodu ve sürüm: https://github.com/acoustid/chromaprint/releases/tag/v1.5.1
- SHA256 ve ayrıntı: `araclar/KAYNAK.txt`
- Lisans: Chromaprint kendi deposundaki lisans dosyasına tabidir; derleme FFmpeg bileşenleri içerir
  (FFmpeg lisansı için bkz. https://ffmpeg.org/legal.html). Telif hakkı ve lisans koşulları ilgili
  projelere aittir; bu depodaki kod lisansını kapsamaz.

## araclar/ffmpeg.exe — FFmpeg 7.1.1 (yalnızca yayın zip'inde)

Ses ve görüntü akışlarını birleştirmek için yt-dlp'nin kullandığı FFmpeg'dir. Yayın zip'ine
[gyan.dev "essentials" derlemesi](https://www.gyan.dev/ffmpeg/builds/) (`ffmpeg version 7.1.1-essentials_build-www.gyan.dev`)
**değiştirilmeden** konur. Bu derleme `--enable-gpl --enable-version3` ile derlenmiştir, yani **GPLv3** altındadır.
Kaynak kodu ve lisans: https://ffmpeg.org/download.html · https://www.gyan.dev/ffmpeg/builds/ · https://www.gnu.org/licenses/gpl-3.0.html
Bu dosya git deposunda tutulmaz (87 MB); kaynaktan çalıştırıyorsanız kendi ffmpeg'inizi PATH'e ya da `araclar\` içine koyun.

---

`fpcalc.exe`'yi kullanmak istemezseniz silebilirsiniz; yalnızca ses analizi çalışmaz, diğer işlevler sürer.
