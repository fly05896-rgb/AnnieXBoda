# Authored By Certified Coders © 2026
# System: SongDownloader Engine (100% Cookie-Free + Innertube Fast Stream + yt-dlp 2026.10.21)

import asyncio
import logging
import os
import shutil
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

from AnnieXMedia.platforms.Youtube import (
    YouTube,
    _SHARED_CODE_CACHE,
    _SHARED_PLAYER_CACHE,
    _SilentYTDLPLogger,
)

logging.basicConfig(level=logging.ERROR)


def LOGGER(name):
    return logging.getLogger(name)


try:
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
    EJSBaseJCP._ENABLE_PREPROCESSED_PLAYER_CACHE = True
except Exception:
    pass

try:
    from yt_dlp.extractor.youtube._video import YoutubeIE
    if not getattr(YoutubeIE, "_annie_cache_patched", False):
        _orig_yt_init = YoutubeIE.__init__

        def _fast_yt_init(self, *args, **kwargs):
            _orig_yt_init(self, *args, **kwargs)
            self._code_cache = _SHARED_CODE_CACHE
            self._player_cache = _SHARED_PLAYER_CACHE

        YoutubeIE.__init__ = _fast_yt_init
        YoutubeIE._annie_cache_patched = True
except Exception:
    pass


class Config:
    if os.path.exists("/dev/shm"):
        DOWNLOAD_PATH = "/dev/shm/AnnieSongDownloads"
    else:
        DOWNLOAD_PATH = os.path.abspath("downloads_songs")

    CACHE_DIR = "/dev/shm/ytdlp_cache" if os.path.exists("/dev/shm") else os.path.abspath("ytdlp_cache")
    MAX_WORKERS = 16


os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)
os.makedirs(Config.CACHE_DIR, exist_ok=True)


class SongDownloaderAPI:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)
        self.force_high_quality = False
        self.playlist_limit = 10
        self.has_ffmpeg = shutil.which("ffmpeg") is not None
        self._stream_cache: dict[str, dict] = {}

    def set_limit(self, limit: int):
        self.playlist_limit = int(limit)

    def open_limit(self):
        self.playlist_limit = 0

    def reset_limit(self):
        self.playlist_limit = 10

    def enable_quality(self):
        self.force_high_quality = True

    def disable_quality(self):
        self.force_high_quality = False

    def _build_ydl_opts(self, is_video: bool, is_playlist: bool = False, out_tmpl: str | None = None) -> dict:
        # 🚀 متوافق 100% مع yt-dlp 2026.10.21 بدون أي كوكيز نهائياً:
        # عدم وضع "js" في player_skip وعدم وضع "formats": ["missing_pot"]
        target_clients = ["web_embedded", "tv_downgraded", "tv_samsung", "visionos", "android"]
        if is_video:
            if self.force_high_quality:
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                fmt = "18/22/b[ext=mp4][height<=480]/b[ext=mp4][height<=720]/b[ext=mp4]/best"
        else:
            fmt = "140/139/251/ba[ext=m4a]/ba/18/b"

        yt_extractor_args = {
            "player_client": target_clients,
            "player_skip": ["webpage", "initial_data"],
            "skip": ["hls", "dash", "translated_subs"],
            "fetch_pot": ["never"],
        }
        if getattr(YouTube, "_visitor_data", None):
            yt_extractor_args["visitor_data"] = [YouTube._visitor_data]

        opts = {
            "format": fmt,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "cookiefile": None,
            "logger": _SilentYTDLPLogger(),
            "cachedir": Config.CACHE_DIR,
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "geo_bypass": False,
            "js_runtimes": {"deno": {}, "node": {}},
            "remote_components": ["ejs:github"],
            "extractor_args": {"youtube": yt_extractor_args},
            "concurrent_fragment_downloads": 16,
            "http_chunk_size": 10485760,
            "buffersize": 1024 * 1024 * 5,
            "retries": 3,
            "socket_timeout": 8,
            "noplaylist": not is_playlist,
            "ignoreerrors": True,
        }
        if out_tmpl:
            opts["outtmpl"] = out_tmpl
        if is_playlist and self.playlist_limit > 0:
            opts["playlistend"] = self.playlist_limit
        if is_video and self.force_high_quality:
            opts["merge_output_format"] = "mp4"
        return opts

    async def extract_stream(self, query_or_link: str, is_video: bool = False) -> dict | None:
        """
        استخراج الرابط المباشر + كافة بيانات الأغنية بدون كوكيز:
        1) أولاً عبر محرك Innertube المباشر (orjson + aiohttp IPv4) في ~80ms بدون تشغيل yt-dlp!
        2) وإذا لم تتوفر الصيغة المباشرة، ينتقل تلقائياً لـ yt-dlp 2026.10.21 بدون كوكيز.
        """
        link = query_or_link.strip()
        if "googleusercontent.com" in link and "v=" in link:
            try:
                link = f"https://www.youtube.com/watch?v={link.split('v=')[1].split('&')[0]}"
            except Exception:
                pass

        cache_key = f"{link}|{'v' if is_video else 'a'}"
        cached_info = self._stream_cache.get(cache_key)
        if cached_info:
            return cached_info

        if not (is_video and self.force_high_quality):
            fast_data = await YouTube.extract_fast_stream_info(link, is_video=is_video)
            if fast_data and fast_data.get("direct_url") and fast_data.get("can_direct_pump"):
                self._stream_cache[cache_key] = fast_data
                self._stream_cache[
                    f"https://www.youtube.com/watch?v={fast_data['vidid']}|{'v' if is_video else 'a'}"
                ] = fast_data
                return fast_data

        if not link.startswith(("http", "www", "ytsearch")):
            link = f"ytsearch1:{link}"

        loop = asyncio.get_running_loop()

        def _extract_only():
            ydl_opts = self._build_ydl_opts(is_video=is_video, is_playlist=False)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=False)
                    if not info:
                        return None
                    if "entries" in info:
                        entries = [e for e in info["entries"] if e]
                        if not entries:
                            return None
                        info = entries[0]

                    direct_url = info.get("url")
                    if not direct_url and info.get("requested_formats"):
                        direct_url = info["requested_formats"][0].get("url")

                    vidid = info.get("id") or ""
                    title = info.get("title") or "Unknown"
                    duration_sec = int(info.get("duration") or 0)
                    mins, secs = divmod(duration_sec, 60)
                    hours, mins = divmod(mins, 60)
                    duration_min = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"

                    thumb = info.get("thumbnail") or (f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg" if vidid else "")
                    if "?" in thumb:
                        thumb = thumb.split("?")[0]

                    filesize = int(info.get("filesize") or info.get("filesize_approx") or 0)
                    if direct_url and not filesize:
                        try:
                            qs = urllib.parse.parse_qs(urllib.parse.urlparse(direct_url).query)
                            if "clen" in qs:
                                filesize = int(qs["clen"][0])
                        except Exception:
                            pass

                    vcodec = info.get("vcodec") or "none"
                    ext = info.get("ext") or ("mp4" if is_video else "m4a")
                    headers = info.get("http_headers") or YouTube.get_stream_headers(direct_url or "")

                    return {
                        "vidid": vidid,
                        "title": title,
                        "duration_sec": duration_sec,
                        "duration_min": duration_min,
                        "thumbnail": thumb,
                        "direct_url": direct_url,
                        "filesize": filesize,
                        "vcodec": vcodec,
                        "ext": ext,
                        "headers": headers,
                        "available_at": info.get("available_at") or 0,
                        "can_direct_pump": bool(
                            direct_url
                            and "m3u8" not in direct_url
                            and (is_video or vcodec == "none" or ext in ("m4a", "mp3", "webm", "ogg", "opus"))
                        ),
                    }
            except Exception as e:
                LOGGER("SongDownloader").error(f"Stream Extract Error: {e}")
                return None

        try:
            data = await loop.run_in_executor(self.pool, _extract_only)
            if data and data.get("vidid"):
                await YouTube._respect_available_at(data)
                self._stream_cache[cache_key] = data
                self._stream_cache[
                    f"https://www.youtube.com/watch?v={data['vidid']}|{'v' if is_video else 'a'}"
                ] = data
                return data
        except Exception:
            pass
        return None

    async def download(self, link: str, is_video: bool = False):
        if "googleusercontent.com" in link and "v=" in link:
            try:
                link = f"https://www.youtube.com/watch?v={link.split('v=')[1].split('&')[0]}"
            except Exception:
                pass

        if not link.startswith(("http", "www", "ytsearch")):
            link = f"ytsearch1:{link}"

        loop = asyncio.get_running_loop()
        is_playlist = "list=" in link and "watch?v=" not in link

        def _download_native():
            suffix = "v" if is_video else "a"
            out_tmpl = os.path.join(Config.DOWNLOAD_PATH, f"%(id)s_{suffix}.%(ext)s")
            ydl_opts = self._build_ydl_opts(is_video=is_video, is_playlist=is_playlist, out_tmpl=out_tmpl)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    if not info:
                        return None

                    if "entries" in info:
                        entries = [e for e in info["entries"] if e]
                        if not entries:
                            return None
                        info = entries[0]

                    filepath = ydl.prepare_filename(info)

                    if not is_video and filepath and os.path.exists(filepath):
                        vcodec = info.get("vcodec") or "none"
                        if vcodec != "none" or filepath.endswith(".mp4"):
                            if self.has_ffmpeg:
                                out_m4a = filepath.rsplit(".", 1)[0] + ".m4a"
                                if os.path.abspath(filepath) == os.path.abspath(out_m4a):
                                    tmp_in = filepath + ".raw.mp4"
                                    os.replace(filepath, tmp_in)
                                    filepath = tmp_in
                                subprocess.run(
                                    [
                                        "ffmpeg",
                                        "-y",
                                        "-loglevel",
                                        "error",
                                        "-i",
                                        filepath,
                                        "-vn",
                                        "-acodec",
                                        "copy",
                                        out_m4a,
                                    ],
                                    check=False,
                                    timeout=15,
                                )
                                if os.path.exists(out_m4a) and os.path.getsize(out_m4a) > 10240:
                                    try:
                                        os.remove(filepath)
                                    except Exception:
                                        pass
                                    return out_m4a

                    return filepath
            except Exception as e:
                LOGGER("SongDownloader").error(f"Download Error: {e}")
            return None

        try:
            file_path = await loop.run_in_executor(self.pool, _download_native)
            if file_path and os.path.exists(file_path):
                return file_path, False
        except Exception:
            pass

        return None, False


SongDownloader = SongDownloaderAPI()
