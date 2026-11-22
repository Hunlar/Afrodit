# Afrodit Telegram Bot

Afrodit, yt-dlp ve FFmpeg tabanlı bir Telegram medya indirme botudur. Desteklenen bir bağlantı özel sohbete veya botun mesajları görebildiği bir gruba gönderildiğinde indirme otomatik başlar; normal bağlantı akışında kalite veya biçim seçme butonları gösterilmez.

## Desteklenen siteler

Instagram, TikTok, Pinterest, YouTube, X/Twitter, Reddit ve yt-dlp tarafından desteklenen diğer siteler. Kullanılabilirlik, ilgili platformların erişim kurallarına ve indirici desteğine bağlıdır.

## Özellikler

- Özel sohbetlerde ve botun mesajları alabildiği gruplarda otomatik bağlantı algılama
- Video için mevcut en iyi kaliteyle otomatik indirme
- Fotoğraf açıklamalarındaki bağlantıları algılama
- `/mp4 <link>` ile video, `/mp3 <link>` ile ses indirme
- İndirme ilerleme mesajları
- Hata, geçmiş ve istatistik bilgileri sunan yönetim paneli
- Geçici dosyaların otomatik temizlenmesi
- FFmpeg ile medya işleme

## Telegram grup ayarı

Telegram'da grup gizlilik modu varsayılan olarak botun yalnızca komutları ve kendisine yöneltilen mesajları görmesine izin verir. Grupta paylaşılan sıradan bağlantıları algılaması için BotFather üzerinden `/setprivacy` komutuyla Afrodit'in gizlilik modunu kapatın. Ardından botu gruptan çıkarıp yeniden ekleyin. Alternatif olarak botu, gerekli mesajları görebilecek şekilde yönetici yapabilirsiniz.

## Heroku dağıtımı

Depoda tek dyno üzerinde çalışan Heroku Container yapılandırması bulunur. Telegram botu, indirme motoru ve yönetim paneli aynı geçici dosya sistemini paylaşır. Adımlar için [HEROKU-DEPLOY.md](HEROKU-DEPLOY.md) dosyasına bakın.

## Gerekli ortam değişkenleri

- `BOT_TOKEN` — @BotFather üzerinden alınan bot tokenı
- `ADMIN_PASSWORD` — yönetim paneli parolası
- `SECRET_KEY` — panel oturumlarını imzalamak için gizli anahtar
- `AFRODIT_EVENT_SECRET` — bot ile panel arasındaki dahili isteklerin doğrulama anahtarı

İsteğe bağlı ayarlar `.env.example` dosyasında listelenmiştir.

## Önemli sınırlar

- Varsayılan resmi Telegram Bot API kullanımı dosya yüklemelerinde 50 MB sınırına tabidir.
- Heroku dyno depolaması kalıcı değildir; indirilen dosyalar ve SQLite geçmişi yeniden başlatma veya yeniden dağıtım sırasında silinebilir.
- Daha büyük dosyalar, kalıcı geçmiş ve kalıcı dosya depolaması için ek altyapı gerekir.
- Her platform veya gönderi için filigransız çıktı garantisi verilemez.

## Lisans

Yukarı akış proje MIT Lisansı beyan etmektedir. Yeniden dağıtımda lisans ve atıf bildirimlerini koruyun.
