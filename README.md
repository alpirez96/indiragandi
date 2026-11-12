# indiragandi

Instagram, YouTube, TikTok ve X videolarını istenen çözünürlükte indiren basit bir web sitesi.
Vercel üzerinde çalışır: statik arayüz (`public/`) + tek bir Python fonksiyonu (`api/index.py`).

## Nasıl çalışır

- `GET /api/info?url=…` — [yt-dlp](https://github.com/yt-dlp/yt-dlp) ile videonun başlığını, kapağını ve çözünürlük seçeneklerini döndürür.
- `GET /api/download?url=…&q=h1080|audio|best&i=…` — Seçilen kaliteyi aynı çağrı içinde indirir, gerekirse ffmpeg ile görüntü+sesi MP4'te birleştirir (ses için MP3), dosyayı akış olarak gönderir ve geçici dosyayı siler.
- `GET /api/health` — ffmpeg / deno / yt-dlp durumunu gösterir.

YouTube imza çözümü için `deno` (pip paketi), birleştirme için `imageio-ffmpeg` içindeki statik ffmpeg kullanılır.

## Ortam değişkenleri (isteğe bağlı)

| Değişken | Açıklama |
| --- | --- |
| `MAX_FILESIZE_MB` | Tek dosya üst sınırı (varsayılan 200). Vercel `/tmp` alanı 512 MB olduğundan ~240'ın üstüne çıkarmayın. |
| `YTDLP_COOKIES` | Netscape `cookies.txt` içeriği (düz ya da base64). Instagram gibi giriş isteyen içerikler için. |
| `YTDLP_PROXY` | yt-dlp için proxy adresi (ör. `http://kullanici:sifre@host:port`). |

## Sınırlar

Vercel Hobby planında bir istek en fazla 300 saniye sürebilir; dosya bu sürede hazırlanıp gönderilmelidir.
Gizli hesaplar, yaş sınırlı içerikler ve canlı yayınlar indirilemez.
