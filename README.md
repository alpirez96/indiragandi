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
| `INSTAGRAM_COOKIES` | Yedek bir Instagram hesabının Netscape `cookies.txt` içeriği. Ses ve giriş isteyen gönderiler için gerekir. |
| `YOUTUBE_COOKIES` | Yedek bir Google hesabının youtube.com çerezleri. Vercel IP'lerine gelen "bot değilim" engelini aşmak için. |
| `TIKTOK_COOKIES`, `X_COOKIES`, `YTDLP_COOKIES` | Diğer platformlar / tüm platformlar için çerezler (isteğe bağlı). |
| `MAX_FILESIZE_MB` | Tek dosya üst sınırı (varsayılan 200). Vercel `/tmp` alanı 512 MB olduğundan ~240'ın üstüne çıkarmayın. |
| `YTDLP_PROXY`, `YTDLP_PROXY_FOR` | İsteğe bağlı proxy ve hangi platformlarda kullanılacağı (varsayılan `YouTube,Instagram,TikTok`). |
| `YTDLP_YT_CLIENTS` | YouTube player_client listesi (ör. `tv,web_safari`). |

Çerezler düz metin (sekme ya da boşluk ayrılmış) veya tek parça base64 olarak verilebilir.
`/api/health` hangi platform için kaç çerez okunduğunu gösterir (değerleri göstermez).

### Çerezleri alma (bilgisayarda)

1. Tarayıcıya "Get cookies.txt LOCALLY" eklentisini kurun ve gizli pencerede çalışmasına izin verin.
2. Gizli pencerede yedek hesapla giriş yapın (YouTube için ardından aynı sekmede `https://www.youtube.com/robots.txt` adresini açın).
3. Eklentiyle o sitenin çerezlerini dışa aktarın ve gizli pencereyi **çıkış yapmadan** kapatın.
4. Dosya içeriğini Vercel → Project → Settings → Environment Variables'a ilgili anahtarla (Production) yapıştırın ve yeniden deploy edin.

## Sınırlar

Vercel Hobby planında bir istek en fazla 300 saniye sürebilir; dosya bu sürede hazırlanıp gönderilmelidir.
Gizli hesaplar, yaş sınırlı içerikler ve canlı yayınlar indirilemez.
