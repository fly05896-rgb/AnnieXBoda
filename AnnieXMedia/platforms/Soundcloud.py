# Authored By Certified Coders © 2026
# System: SoundCloud API (Single-Request Extraction & Direct Streaming)

import asyncio
import os
import re
from typing import Any, Dict, Tuple, Union

from yt_dlp import YoutubeDL

from AnnieXMedia.platforms.Youtube import YouTube
from AnnieXMedia.utils.formatters import seconds_to_min

_SC_RE = re.compile(r"^https?://(soundcloud\.com|on\.soundcloud\.com|m\.soundcloud\.com)/.+", re.I)


class SoundAPI:
    def __init__(self):
        self.opts = {
            "format": "bestaudio/best",
            "outtmpl": "downloads/sc_%(id)s.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "force_ipv4": True,
            "nocheckcertificate": True,
        }

    async def valid(self, link: str) -> bool:
        return bool(link and _SC_RE.match(link))

    async def download(self, url: str) -> Union[Tuple[Dict[str, Any], str], bool]:
        """
        دمج استخراج بيانات SoundCloud وجلب الرابط المباشر (أو التحميل) في خطوة واحدة فقط
        بدون استدعاء yt-dlp مرتين!
        """
        os.makedirs("downloads", exist_ok=True)
        loop = asyncio.get_running_loop()

        def _extract_and_resolve(u: str):
            # نحاول أولاً استخراج الرابط المباشر بدون تحميل (أسرع بـ 10 مرات)
            fast_opts = {**self.opts, "skip_download": True}
            with YoutubeDL(fast_opts) as ydl:
                info = ydl.extract_info(u, download=False)
                if not info or info.get("_type") == "playlist":
                    return None, None
                _type = str(info.get("_type", ""))
                if _type in ("url", "url_transparent") and info.get("url"):
                    info = ydl.extract_info(info["url"], download=False)
                direct_url = info.get("url")
                if direct_url and not str(direct_url).endswith(".m3u8"):
                    return info, direct_url

            # إذا كان الرابط HLS مقسم يتطلب تجميع، نقوم بتحميله مباشرة في خطوة واحدة
            dl_opts = {**self.opts, "skip_download": False}
            with YoutubeDL(dl_opts) as ydl:
                info = ydl.extract_info(u, download=True)
                if not info:
                    return None, None
                filepath = ydl.prepare_filename(info)
                return info, filepath

        try:
            info, out_path = await loop.run_in_executor(YouTube._pool, _extract_and_resolve, url)
        except Exception:
            return False

        if not info or not out_path:
            return False

        title = (info.get("title") or "SoundCloud Track").strip()
        title = re.sub(r'[\\/*?:"<>|]', "", title)

        try:
            duration_sec = int(info.get("duration") or 0)
        except Exception:
            duration_sec = 0

        uploader = info.get("uploader") or "SoundCloud"
        thumb = (
            info.get("thumbnail")
            or (info.get("thumbnails") or [{}])[0].get("url")
            or ""
        )

        details = {
            "title": title,
            "duration_sec": duration_sec,
            "duration_min": seconds_to_min(duration_sec),
            "uploader": uploader,
            "thumb": thumb,
            "filepath": out_path,
            "path": out_path,
        }
        return details, out_path
