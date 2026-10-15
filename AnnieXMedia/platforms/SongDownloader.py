import asyncio
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

logging.basicConfig(level=logging.ERROR)
def LOGGER(name): return logging.getLogger(name)

HAS_ARIA2C = bool(shutil.which("aria2c"))


class Config:
    if os.path.exists("/dev/shm"):
        DOWNLOAD_PATH = "/dev/shm/AnnieSongDownloads"
    else:
        DOWNLOAD_PATH = os.path.abspath("downloads_songs")

    CACHE_DIR = "/tmp/ytdlp_cache"
    MAX_WORKERS = 12


os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)
os.makedirs(Config.CACHE_DIR, exist_ok=True)


class SongDownloaderAPI:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)
        self.force_high_quality = False
        self.playlist_limit = 10

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

    async def download(self, link: str, is_video: bool = False):
        if "googleusercontent.com" in link and "v=" in link:
            try:
                link = f"https://www.youtube.com/watch?v={link.split('v=')[1].split('&')[0]}"
            except Exception:
                pass

        # استخدام البحث السريع عبر IPv4 من YouTubeAPI بدلاً من VideosSearch المعطل
        if not link.startswith(("http", "www")):
            try:
                from AnnieXMedia.platforms.Youtube import YouTube
                results = await YouTube.search(link, limit=1)
                if results and results[0].get("vidid"):
                    link = f"https://www.youtube.com/watch?v={results[0]['vidid']}"
                else:
                    link = f"ytsearch1:{link}"
            except Exception:
                link = f"ytsearch1:{link}"

        # فحص وجود الملف مسبقاً في الرام (/dev/shm)
        if "v=" in link:
            vid_id = link.split("v=")[1].split("&")[0]
            if len(vid_id) == 11:
                exts = (".mp4",) if is_video else (".m4a", ".mp3", ".webm")
                for ext in exts:
                    existing = os.path.join(Config.DOWNLOAD_PATH, f"{vid_id}{ext}")
                    if os.path.exists(existing) and os.path.getsize(existing) > 1024:
                        return existing, False

        loop = asyncio.get_running_loop()

        if is_video:
            if self.force_high_quality:
                target_clients = ["tv_samsung", "visionos"]
                fmt = "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                skip_js = []
            else:
                # للفيديو السريع: visionos يعطي صيغة 18 (360p MP4 مدمج جاهز) بدون JS
                target_clients = ["visionos", "tv_samsung"]
                fmt = "18/b[ext=mp4][height<=360]/b[ext=mp4][height<=480]/b[ext=mp4]/best"
                skip_js = ["webpage", "configs", "initial_data", "js"]
        else:
            # للصوتيات: tv_samsung و android يوفران صيغة 139 (48kbps m4a بحجم ~1MB فقط!) وصيغة 140
            # وضعنا tv_samsung و android أولاً لمنع visionos من إرجاع فيديو 12MB!
            target_clients = ["tv_samsung", "android", "web_embedded"]
            fmt = (
                "140/ba[ext=m4a]/ba/b"
                if self.force_high_quality
                else "139/249/250/140/ba[abr<=128][ext=m4a]/ba[ext=m4a]/ba/18"
            )
            skip_js = ["webpage", "configs", "initial_data"]

        is_playlist = "list=" in link and "watch?v=" not in link

        def _download_native():
            out_tmpl = os.path.join(Config.DOWNLOAD_PATH, "%(id)s.%(ext)s")

            ydl_opts = {
                "format": fmt,
                "outtmpl": out_tmpl,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "nocheckcertificate": True,
                "cookiefile": None,
                "force_ipv4": True,
                "source_address": "0.0.0.0",
                "geo_bypass": False,
                "cachedir": Config.CACHE_DIR,
                "js_runtimes": {"deno": {}, "node": {}},
                "remote_components": ["ejs:github"],
                "extractor_args": {
                    "youtube": {
                        "player_client": target_clients,
                        "player_skip": skip_js,
                        "skip": ["hls", "dash", "translated_subs"],
                        "formats": ["missing_pot"],
                        "fetch_pot": ["never"],
                    }
                },
                "concurrent_fragment_downloads": 16,
                "http_chunk_size": 10485760,
                "buffersize": 1024 * 1024 * 5,
                "retries": 3,
                "socket_timeout": 8,
                "noplaylist": not is_playlist,
                "ignoreerrors": True,
            }

            if HAS_ARIA2C and not (is_video and self.force_high_quality):
                ydl_opts["external_downloader"] = "aria2c"
                ydl_opts["external_downloader_args"] = {
                    "aria2c": [
                        "-x16",
                        "-s16",
                        "-j16",
                        "-k1M",
                        "--file-allocation=none",
                        "--console-log-level=error",
                        "--summary-interval=0",
                        "--download-result=hide",
                    ]
                }

            if is_playlist and self.playlist_limit > 0:
                ydl_opts["playlistend"] = self.playlist_limit

            if is_video and self.force_high_quality:
                ydl_opts["merge_output_format"] = "mp4"

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    if not info:
                        return None

                    if "entries" in info:
                        entries = [e for e in info["entries"] if e]
                        if entries:
                            return ydl.prepare_filename(entries[0])
                    return ydl.prepare_filename(info)
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
