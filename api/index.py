"""indiragandi — video indirme API'si (Vercel Python fonksiyonu, saf ASGI).

Uç noktalar
  GET /api/info?url=...                 -> videonun başlığı, kapak görseli ve çözünürlük seçenekleri
  GET /api/download?url=...&q=...&i=... -> seçilen kalitedeki dosya (akış olarak)
  GET /api/health                       -> çalışma ortamı bilgisi

Notlar
  * Platform bağlantıları (özellikle YouTube) isteği yapan IP'ye bağlıdır. Bu yüzden
    indirme isteği bilgiyi aynı çağrı içinde yeniden çıkarır, dosyayı /tmp'ye indirir,
    gerekirse ffmpeg ile ses+görüntüyü birleştirir ve sonra kullanıcıya akıtır.
  * Kalite seçimi her iki uçta da aynı saf Python fonksiyonuyla yapılır (choose_formats),
    böylece arayüzde gösterilen seçenek ile inen dosya birbirini tutar.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import uuid

TMP = tempfile.gettempdir()
WORK_ROOT = os.path.join(TMP, "indiragandi")
MAX_BYTES = int(os.environ.get("MAX_FILESIZE_MB", "200")) * 1024 * 1024
MAX_ITEMS = 10
MAX_OPTIONS = 6
MP3_KBPS = 192

# Desteklenen alan adları -> platform adı
PLATFORMS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "youtube-nocookie.com": "YouTube",
    "instagram.com": "Instagram",
    "instagr.am": "Instagram",
    "tiktok.com": "TikTok",
    "x.com": "X",
    "twitter.com": "X",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
    "vimeo.com": "Vimeo",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
    "dailymotion.com": "Dailymotion",
    "dai.ly": "Dailymotion",
    "twitch.tv": "Twitch",
}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
BARE_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s<>\"']*", re.I)


# --------------------------------------------------------------------------- #
# Ortam: ffmpeg, deno (YouTube imza çözümü için), çerez ve proxy ayarları
# --------------------------------------------------------------------------- #

_env_cache: dict | None = None


def _ensure_exec(path: str | None, name: str) -> str | None:
    """Paket içindeki ikili dosya çalıştırılabilir değilse /tmp'ye kopyalayıp izin ver."""
    if not path or not os.path.isfile(path):
        return None
    if os.access(path, os.X_OK):
        return path
    dst_dir = os.path.join(TMP, "bin")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    if not os.path.isfile(dst):
        shutil.copyfile(path, dst)
    os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


def _find_deno() -> str | None:
    try:
        import deno  # type: ignore

        return _ensure_exec(deno.find_deno_bin(), "deno")
    except Exception:
        pass
    # pip --target / vendor klasörü düzenleri için yedek arama
    for base in sys.path:
        for rel in ("bin/deno", "../bin/deno", "../../bin/deno", "../../../bin/deno"):
            p = os.path.normpath(os.path.join(base, rel))
            if os.path.isfile(p):
                return _ensure_exec(p, "deno")
    found = shutil.which("deno")
    return _ensure_exec(found, "deno") if found else None


def _find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore

        return _ensure_exec(imageio_ffmpeg.get_ffmpeg_exe(), "ffmpeg")
    except Exception:
        found = shutil.which("ffmpeg")
        return found


def runtime_env() -> dict:
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    # Lambda'da yalnızca /tmp yazılabilir
    for key, sub in (("DENO_DIR", "deno-cache"), ("XDG_CACHE_HOME", "cache")):
        os.environ.setdefault(key, os.path.join(TMP, sub))
    home = os.environ.get("HOME")
    if not home or not os.access(home, os.W_OK):
        os.environ["HOME"] = TMP
    os.environ.setdefault("DENO_NO_UPDATE_CHECK", "1")

    _env_cache = {
        "ffmpeg": _find_ffmpeg(),
        "deno": _find_deno(),
    }
    return _env_cache


COOKIE_ENVS = {
    "YouTube": "YOUTUBE_COOKIES",
    "Instagram": "INSTAGRAM_COOKIES",
    "TikTok": "TIKTOK_COOKIES",
    "X": "X_COOKIES",
}


def _normalize_cookies(raw: str) -> list[str]:
    """Netscape cookies.txt içeriğini satır satır düzeltir.

    Vercel paneline yapıştırırken sekmeler boşluğa dönüşebildiği için alanlar
    boşluklara göre de ayrılır. Tek parça base64 metin de kabul edilir.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    if not any(ch.isspace() for ch in raw):
        try:
            raw = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        except Exception:
            return []
    lines = []
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t") if line.count("\t") >= 6 else line.split(None, 6)
        if len(parts) == 6:  # değeri boş çerez
            parts.append("")
        if len(parts) != 7:
            continue
        lines.append("\t".join(p.strip() for p in parts))
    return lines


def _cookie_file(platform: str) -> str | None:
    """Platforma ait çerezleri (ve genel YTDLP_COOKIES'i) geçici bir cookies.txt'ye yazar."""
    lines: list[str] = []
    for name in (COOKIE_ENVS.get(platform), "YTDLP_COOKIES"):
        if name:
            lines += _normalize_cookies(os.environ.get(name, ""))
    if not lines:
        return None
    path = os.path.join(TMP, f"cookies-{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n")
    return path


def _proxy_for(platform: str) -> str | None:
    proxy = os.environ.get("YTDLP_PROXY", "").strip()
    if not proxy:
        return None
    allowed = os.environ.get("YTDLP_PROXY_FOR", "YouTube,Instagram,TikTok")
    names = {n.strip().lower() for n in allowed.split(",") if n.strip()}
    return proxy if ("all" in names or platform.lower() in names) else None


class _Logger:
    """yt-dlp çıktısını yakalar; 'info' satırları da debug() üzerinden gelir."""

    def __init__(self):
        self.lines: list[str] = []

    def _keep(self, msg):
        self.lines.append(str(msg))
        del self.lines[:-40]

    def debug(self, msg):
        if not str(msg).startswith("[debug] "):
            self._keep(msg)

    def info(self, msg):
        self._keep(msg)

    def warning(self, msg):
        self._keep(msg)

    def error(self, msg):
        self._keep(msg)


def ydl_opts(platform: str = "", **extra) -> dict:
    env = runtime_env()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "playlistend": MAX_ITEMS,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "cachedir": os.path.join(TMP, "yt-dlp-cache"),
        "allowed_extractors": ["default", "-generic"],
        "max_filesize": MAX_BYTES,
        "overwrites": True,
        "logger": _Logger(),
    }
    if env["ffmpeg"]:
        opts["ffmpeg_location"] = env["ffmpeg"]
    if env["deno"]:
        opts["js_runtimes"] = {"deno": {"path": env["deno"]}}
    proxy = _proxy_for(platform)
    if proxy:
        opts["proxy"] = proxy
    clients = [c.strip() for c in os.environ.get("YTDLP_YT_CLIENTS", "").split(",") if c.strip()]
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    opts.update(extra)
    return opts


# --------------------------------------------------------------------------- #
# URL doğrulama
# --------------------------------------------------------------------------- #


class UserError(Exception):
    def __init__(self, message: str, status: int = 400, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def normalize_url(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    m = URL_RE.search(text)
    if m:
        url = m.group(0)
    else:
        m = BARE_RE.search(text)
        if not m:
            raise UserError("Geçerli bir video bağlantısı yapıştırın.")
        url = "https://" + m.group(0)
    url = url.rstrip(").,;!")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return url, name
    raise UserError(
        "Bu site desteklenmiyor. Instagram, YouTube, TikTok veya X bağlantısı deneyin."
    )


# --------------------------------------------------------------------------- #
# Format seçimi (bilgi ve indirme uçları aynı mantığı kullanır)
# --------------------------------------------------------------------------- #


def _none(v) -> bool:
    return v == "none"


def _usable(f: dict) -> bool:
    if not f.get("url") and not f.get("fragments"):
        return False
    if f.get("has_drm"):
        return False
    proto = str(f.get("protocol") or "")
    if proto.startswith("mhtml") or f.get("ext") == "mhtml":
        return False
    note = str(f.get("format_note") or "").lower()
    if "watermark" in note or "storyboard" in note:
        return False
    return True


def _is_video(f: dict) -> bool:
    return not _none(f.get("vcodec")) and bool(f.get("height") or f.get("width"))


def _is_audio_only(f: dict) -> bool:
    return _none(f.get("vcodec")) and not _none(f.get("acodec"))


def _has_audio(f: dict) -> bool:
    return not _none(f.get("acodec"))


def _is_video_any(f: dict) -> bool:
    """Boyut bilgisi olmasa da görüntü içeren (ya da içerdiği bilinmeyen) format."""
    return not _none(f.get("vcodec"))


def _vcodec_rank(f: dict) -> int:
    c = str(f.get("vcodec") or "").lower()
    if not c or c.startswith(("avc", "h264")):
        return 0
    if c.startswith(("hev", "hvc", "h265", "bytevc1")):
        return 1
    if c.startswith(("vp9", "vp09")):
        return 2
    if c.startswith("av01"):
        return 3
    return 4


def _acodec_rank(f: dict) -> int:
    c = str(f.get("acodec") or "").lower()
    if c.startswith(("mp4a", "aac")) or f.get("ext") == "m4a":
        return 0
    if not c:
        return 1
    if c.startswith("opus"):
        return 2
    return 3


def _proto_rank(f: dict) -> int:
    p = str(f.get("protocol") or "https")
    if p in ("https", "http"):
        return 0
    if p.startswith("m3u8"):
        return 1
    return 2


def _size(f: dict, duration: float | None) -> int | None:
    s = f.get("filesize") or f.get("filesize_approx")
    if s:
        return int(s)
    tbr = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0))
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def _short_side(f: dict) -> int:
    w, h = f.get("width"), f.get("height")
    if w and h:
        return int(min(w, h))
    return int(h or w or 0)


def _height_key(f: dict) -> int:
    return int(f.get("height") or f.get("width") or 0)


def _best_audio(formats: list[dict]) -> dict | None:
    audios = [f for f in formats if _usable(f) and _is_audio_only(f)]
    if not audios:
        return None
    return min(
        audios,
        key=lambda f: (
            _acodec_rank(f),
            _proto_rank(f),
            "drc" in str(f.get("format_id", "")),
            -(f.get("abr") or f.get("tbr") or 0),
        ),
    )


def choose_formats(formats: list[dict], q: str) -> list[dict]:
    """q: 'h<yükseklik>' (video) ya da 'audio'. Seçilen 1 veya 2 formatı döndürür."""
    formats = [f for f in (formats or []) if _usable(f)]
    if not formats:
        return []

    if q == "audio":
        a = _best_audio(formats)
        if a:
            return [a]
        av = [f for f in formats if _is_video_any(f) and _has_audio(f)]
        if av:  # ses ayrı değilse en küçük videodan sesi çıkar
            return [min(av, key=lambda f: (_height_key(f), _proto_rank(f)))]
        return []

    if q == "best":
        # yt-dlp formatları kötüden iyiye sıralar; sesi olan en iyi formatı tercih et
        anyv = [f for f in formats if _is_video_any(f)]
        if not anyv:
            return []
        with_audio = [f for f in anyv if _has_audio(f)]
        best = (with_audio or anyv)[-1]
        if _has_audio(best):
            return [best]
        audio = _best_audio(formats)
        return [best, audio] if audio else [best]

    try:
        target = int(q.lstrip("h"))
    except ValueError:
        target = 10**6
    videos = [f for f in formats if _is_video(f)]
    if not videos:
        return choose_formats(formats, "best")
    exact = [f for f in videos if _height_key(f) == target]
    if not exact:
        lower = [f for f in videos if _height_key(f) <= target]
        pool_h = max(map(_height_key, lower)) if lower else min(map(_height_key, videos))
        exact = [f for f in videos if _height_key(f) == pool_h]

    best = min(
        exact,
        key=lambda f: (
            _vcodec_rank(f),
            _proto_rank(f),
            0 if _has_audio(f) else 1,
            -(f.get("tbr") or f.get("vbr") or 0),
        ),
    )
    if _has_audio(best):
        return [best]
    audio = _best_audio(formats)
    return [best, audio] if audio else [best]


def build_options(info: dict) -> list[dict]:
    formats = info.get("formats") or []
    duration = info.get("duration")
    heights = sorted(
        {_height_key(f) for f in formats if _usable(f) and _is_video(f)}, reverse=True
    )
    candidates, seen = [], set()
    for h in heights:
        chosen = choose_formats(formats, f"h{h}")
        if not chosen:
            continue
        key = tuple(f.get("format_id") for f in chosen)
        if key in seen:
            continue
        seen.add(key)
        video = chosen[0]
        short = _short_side(video)
        if short < 120:
            continue
        sizes = [_size(f, duration) for f in chosen]
        size = sum(sizes) if all(sizes) else None
        label = f"{short}p"
        if short >= 2100:
            label += " 4K"
        elif short >= 1400:
            label += " 2K"
        candidates.append(
            {
                "q": f"h{h}",
                "label": label,
                "kind": "video",
                "ext": "mp4",
                "size": size,
                "tooBig": bool(size and size > MAX_BYTES),
                "silent": len(chosen) == 1 and not _has_audio(video),
            }
        )

    # Boyut bilgisi olmayan platformlar için tek "en iyi" seçeneği
    if not candidates:
        chosen = choose_formats(formats, "best")
        if chosen:
            sizes = [_size(f, duration) for f in chosen]
            size = sum(sizes) if all(sizes) else None
            note = str(chosen[0].get("format_note") or "").strip()
            candidates.append(
                {
                    "q": "best",
                    "label": note.upper() if note and len(note) <= 8 else "En iyi",
                    "kind": "video",
                    "ext": "mp4",
                    "size": size,
                    "tooBig": bool(size and size > MAX_BYTES),
                    "silent": len(chosen) == 1 and not _has_audio(chosen[0]),
                }
            )

    # Sığanları göster; hiçbiri sığmıyorsa en küçüğünü "çok büyük" olarak göster
    options = [o for o in candidates if not o["tooBig"]][:MAX_OPTIONS]
    if not options and candidates:
        options = candidates[-1:]

    if choose_formats(formats, "audio"):
        size = int(duration * MP3_KBPS * 1000 / 8) if duration else None
        options.append(
            {
                "q": "audio",
                "label": "Ses",
                "kind": "audio",
                "ext": "mp3",
                "size": size,
                "tooBig": bool(size and size > MAX_BYTES),
                "silent": False,
            }
        )
    return options


# --------------------------------------------------------------------------- #
# Hata mesajları
# --------------------------------------------------------------------------- #

_ERRORS = [
    (r"not a bot|confirm you.?re not|sign in to confirm",
     "YouTube şu anda sunucumuzdan gelen istekleri engelliyor. Diğer platformlar çalışıyor.", 503),
    (r"IP address is blocked|blocked in your (country|region)|not available in your (country|region)|geo.?restrict",
     "Bu video sunucumuzun bölgesinde engelli, indirilemiyor.", 451),
    (r"larger than max-filesize|File is larger",
     "Dosya çok büyük. Daha düşük bir çözünürlük seçin.", 413),
    (r"private|login required|log in|cookies|rate-limit|rate limit|requires authentication",
     "Bu içerik giriş yapmadan görüntülenemiyor (gizli hesap ya da geçici limit).", 403),
    (r"age[- ]restrict|confirm your age|age verification|inappropriate for some users",
     "Bu içerik yaş doğrulaması istiyor, indirilemiyor.", 403),
    (r"live event|is live|livestream|currently live",
     "Canlı yayınlar indirilemiyor.", 400),
    (r"no video|there.?s no video|no formats|Requested format is not available|no media",
     "Bu bağlantıda indirilebilir bir video bulunamadı.", 404),
    (r"unavailable|not found|404|removed|deleted|does not exist",
     "Video bulunamadı ya da kaldırılmış.", 404),
    (r"Unsupported URL",
     "Bu bağlantı desteklenmiyor. Videonun kendi bağlantısını yapıştırın.", 400),
    (r"timed out|timeout|Connection reset|Temporary failure",
     "Platforma bağlanırken zaman aşımı oldu. Tekrar deneyin.", 504),
]


def friendly_error(exc: Exception) -> UserError:
    if isinstance(exc, UserError):
        return exc
    raw = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "").strip()
    for pattern, msg, status in _ERRORS:
        if re.search(pattern, raw, re.I):
            return UserError(msg, status, raw[:300])
    return UserError("Video alınamadı. Bağlantıyı kontrol edip tekrar deneyin.", 502, raw[:300])


# --------------------------------------------------------------------------- #
# İş mantığı
# --------------------------------------------------------------------------- #


def _first_format_selector(ctx):
    fmts = ctx.get("formats") or []
    return iter(fmts[-1:])


def _entries(info: dict) -> list[tuple[int, dict]]:
    """(oynatma listesindeki konum, kayıt) çiftleri; tek video için konum 0."""
    if info.get("_type") in ("playlist", "multi_video"):
        out = []
        for pos, e in enumerate(info.get("entries") or [], start=1):
            if e and e.get("formats"):
                out.append((int(e.get("playlist_index") or pos), e))
        return out[:MAX_ITEMS]
    return [(0, info)]


def get_info(text: str) -> dict:
    import yt_dlp

    url, platform = normalize_url(text)
    cookie = _cookie_file(platform)
    opts = ydl_opts(platform, format=_first_format_selector, skip_download=True)
    if cookie:
        opts["cookiefile"] = cookie
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise friendly_error(exc)
    finally:
        if cookie:
            _silent_remove(cookie)

    if not info:
        raise UserError("Bu bağlantıda indirilebilir bir video bulunamadı.", 404)
    if info.get("is_live"):
        raise UserError("Canlı yayınlar indirilemiyor.")

    entries = _entries(info)
    if not entries:
        raise UserError(
            "Bu bir oynatma listesi ya da profil bağlantısı. Tek bir videonun bağlantısını yapıştırın."
        )

    items = []
    for pos, e in entries:
        options = build_options(e)
        if not options:
            continue
        items.append(
            {
                "index": pos,
                "title": (e.get("title") or info.get("title") or platform + " videosu")[:200],
                "thumbnail": e.get("thumbnail") or info.get("thumbnail"),
                "duration": e.get("duration"),
                "uploader": e.get("uploader") or e.get("channel") or info.get("uploader"),
                "options": options,
            }
        )
    if not items:
        raise UserError("Bu bağlantıda indirilebilir bir video bulunamadı.", 404)
    return {"platform": platform, "url": url, "items": items, "maxBytes": MAX_BYTES}


def _silent_remove(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    except OSError:
        pass


def _cleanup_stale(max_age: int = 900):
    try:
        now = time.time()
        for name in os.listdir(WORK_ROOT):
            p = os.path.join(WORK_ROOT, name)
            if now - os.path.getmtime(p) > max_age:
                _silent_remove(p)
    except OSError:
        pass


def safe_filename(title: str, suffix: str, ext: str) -> tuple[str, str]:
    """(ascii_ad, utf8_ad) döndürür."""
    title = re.sub(r"[\\/:*?\"<>|\x00-\x1f#%]+", " ", title or "video")
    title = re.sub(r"\s+", " ", title).strip(" .") or "video"
    title = title[:80].rstrip()
    utf8 = f"{title} [{suffix}].{ext}"
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    ascii_ = unicodedata.normalize("NFKD", utf8.translate(tr)).encode("ascii", "ignore").decode()
    ascii_ = re.sub(r"[^A-Za-z0-9 ._\-\[\]()]+", "", ascii_)
    ascii_ = re.sub(r"\s+", " ", ascii_).strip() or f"video.{ext}"
    return ascii_, utf8


def prepare_download(text: str, q: str, index: int) -> dict:
    import yt_dlp

    url, platform = normalize_url(text)
    if q not in ("audio", "best") and not re.fullmatch(r"h\d{2,5}", q or ""):
        raise UserError("Geçersiz kalite seçimi.")

    os.makedirs(WORK_ROOT, exist_ok=True)
    _cleanup_stale()
    work = os.path.join(WORK_ROOT, uuid.uuid4().hex)
    os.makedirs(work)
    meta: dict = {}

    def selector(ctx):
        chosen = choose_formats(ctx.get("formats") or [], q)
        if not chosen:
            return
        if len(chosen) == 1:
            meta["formats"] = chosen
            yield chosen[0]
            return
        v, a = chosen
        meta["formats"] = chosen
        yield {
            "format_id": f"{v['format_id']}+{a['format_id']}",
            "ext": "mp4",
            "requested_formats": [v, a],
            "protocol": f"{v.get('protocol')}+{a.get('protocol')}",
            "vcodec": v.get("vcodec"),
            "acodec": a.get("acodec"),
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": v.get("fps"),
        }

    opts = ydl_opts(
        platform,
        format=selector,
        outtmpl={"default": os.path.join(work, "media.%(ext)s")},
        paths={"home": work, "temp": work},
        merge_output_format="mp4",
    )
    if index > 0:
        opts["playlist_items"] = str(index)
        opts["noplaylist"] = False
    if q == "audio":
        opts["final_ext"] = "mp3"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(MP3_KBPS)}
        ]
    else:
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]

    cookie = _cookie_file(platform)
    if cookie:
        opts["cookiefile"] = cookie
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        _silent_remove(work)
        raise friendly_error(exc)
    finally:
        if cookie:
            _silent_remove(cookie)

    files = [
        os.path.join(work, n)
        for n in os.listdir(work)
        if not n.endswith((".part", ".ytdl", ".temp", ".json")) and not n.startswith(".")
    ]
    files = [p for p in files if os.path.isfile(p) and os.path.getsize(p) > 0]
    if not files:
        _silent_remove(work)
        logger = opts.get("logger")
        lines = getattr(logger, "lines", [])
        big = [ln for ln in lines if re.search(r"max-filesize|larger than", ln, re.I)]
        detail = "; ".join(big or lines[-3:])
        if re.search(r"max-filesize|larger than", detail, re.I):
            raise UserError("Dosya çok büyük. Daha düşük bir çözünürlük seçin.", 413, detail)
        raise UserError("Dosya hazırlanamadı. Başka bir kalite deneyin.", 502, detail[:300])

    path = max(files, key=os.path.getsize)
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "mp4"
    entry = info
    if info and info.get("_type") in ("playlist", "multi_video"):
        ents = [e for e in (info.get("entries") or []) if e]
        entry = ents[0] if ents else info
    title = (entry or {}).get("title") or (info or {}).get("title") or platform

    if q == "audio":
        suffix = "ses"
    else:
        v = (meta.get("formats") or [{}])[0]
        suffix = f"{_short_side(v)}p" if _short_side(v) else "video"
    ascii_name, utf8_name = safe_filename(title, suffix, ext)
    ctype = {
        "mp4": "video/mp4",
        "m4a": "audio/mp4",
        "mp3": "audio/mpeg",
        "webm": "video/webm",
        "mov": "video/quicktime",
    }.get(ext, "application/octet-stream")
    return {
        "path": path,
        "work": work,
        "size": os.path.getsize(path),
        "ascii_name": ascii_name,
        "utf8_name": utf8_name,
        "ctype": ctype,
    }


def health() -> dict:
    import yt_dlp

    env = runtime_env()
    out = {
        "ok": True,
        "yt_dlp": yt_dlp.version.__version__,
        "python": sys.version.split()[0],
        "ffmpeg": bool(env["ffmpeg"]),
        "deno": bool(env["deno"]),
        "cookies": {p: len(_normalize_cookies(os.environ.get(e, ""))) for p, e in COOKIE_ENVS.items()},
        "proxyFor": [p for p in ("YouTube", "Instagram", "TikTok", "X") if _proxy_for(p)],
        "maxMB": MAX_BYTES // (1024 * 1024),
    }
    try:
        import yt_dlp_ejs  # type: ignore  # noqa: F401

        out["ejs"] = True
    except Exception:
        out["ejs"] = False
    return out


# --------------------------------------------------------------------------- #
# ASGI uygulaması
# --------------------------------------------------------------------------- #

BASE_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
]


async def _json(send, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": BASE_HEADERS
            + [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_file(send, prep: dict):
    disp = "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
        prep["ascii_name"].replace('"', ""), urllib.parse.quote(prep["utf8_name"], safe="")
    )
    try:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": BASE_HEADERS
                + [
                    (b"content-type", prep["ctype"].encode()),
                    (b"content-length", str(prep["size"]).encode()),
                    (b"content-disposition", disp.encode("latin-1", "ignore")),
                ],
            }
        )
        with open(prep["path"], "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        _silent_remove(prep["work"])


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    path = scope.get("path") or "/"
    params = urllib.parse.parse_qs((scope.get("query_string") or b"").decode("utf-8", "ignore"))

    def param(name: str, default: str = "") -> str:
        vals = params.get(name)
        return vals[0] if vals else default

    action = param("action") or path.rstrip("/").rsplit("/", 1)[-1]

    if scope.get("method") not in ("GET", "HEAD"):
        await _json(send, 405, {"error": "Yalnızca GET desteklenir."})
        return

    try:
        if action == "health":
            await _json(send, 200, await asyncio.to_thread(health))
        elif action == "info":
            await _json(send, 200, await asyncio.to_thread(get_info, param("url")))
        elif action == "download":
            try:
                index = max(0, min(MAX_ITEMS, int(param("i", "0") or 0)))
            except ValueError:
                index = 0
            prep = await asyncio.to_thread(prepare_download, param("url"), param("q", "audio"), index)
            await _send_file(send, prep)
        else:
            await _json(send, 404, {"error": "Bulunamadı."})
    except UserError as err:
        await _json(send, err.status, {"error": err.message, "detail": err.detail})
    except Exception as exc:  # noqa: BLE001
        err = friendly_error(exc)
        await _json(send, err.status, {"error": err.message, "detail": err.detail})
