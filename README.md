# indiragandi

TikTok, X, Pinterest ve Twitch (klip) videolarını istenen çözünürlükte indiren basit bir web sitesi.
YouTube ve Instagram, Vercel sunucu IP'lerini engellediği için desteklenmiyor.
Vercel üzerinde çalışır: statik arayüz (`public/`) + tek bir Python fonksiyonu (`api/index.py`).

## Nasıl çalışır

- `GET /api/info?url=…` — [yt-dlp](https://github.com/yt-dlp/yt-dlp) ile videonun başlığını, kapağını ve çözünürlük seçeneklerini döndürür.
- `GET /api/download?url=…&q=h1080|audio|best&i=…` — Seçilen kaliteyi aynı çağrı içinde indirir, gerekirse ffmpeg ile görüntü+sesi MP4'te birleştirir (ses için MP3), dosyayı akış olarak gönderir ve geçici dosyayı siler.
- `GET /api/health` — ffmpeg / yt-dlp durumunu gösterir.

Birleştirme ve MP3 dönüştürme için `imageio-ffmpeg` içindeki statik ffmpeg kullanılır. TikTok kısa bağlantıları (vm./vt.tiktok.com) sunucuda tam adrese çevrilir.

## Ortam değişkenleri (isteğe bağlı)

| Değişken | Açıklama |
| --- | --- |
| `MAX_FILESIZE_MB` | Tek dosya üst sınırı (varsayılan 200). Vercel `/tmp` alanı 512 MB olduğundan ~240'ın üstüne çıkarmayın. |
| `TIKTOK_COOKIES`, `X_COOKIES`, `YTDLP_COOKIES` | Netscape `cookies.txt` içeriği (düz, boşluk ayrılmış ya da base64). Normalde gerekmez. |
| `YTDLP_PROXY`, `YTDLP_PROXY_FOR` | İsteğe bağlı proxy ve hangi platformlarda kullanılacağı (varsayılan `all`). |

## Sınırlar

Vercel Hobby planında bir istek en fazla 300 saniye sürebilir; dosya bu sürede hazırlanıp gönderilmelidir.
Gizli hesaplar, yaş sınırlı içerikler ve canlı yayınlar indirilemez.
