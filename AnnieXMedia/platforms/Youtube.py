import asyncio
import re
import logging
import os
import time
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import aiofiles
from pyrogram import enums
from yt_dlp import YoutubeDL
from youtubesearchpython.aio import VideosSearch

log = logging.getLogger("AnnieXMedia.YouTube")


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        # Pool مخصص للاستخراج الفوري بدون انتظار الـ Default Executor
        self._pool = ThreadPoolExecutor(max_workers=16)
        # كاش للروابط المباشرة (الصلاحية ساعة واحدة) للاستجابة في 0ms عند التكرار
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 3600

        # إعدادات صاروخية متوافقة مع yt-dlp 2026.08.19:
        # 1. visionos + android لا يحتاجان لتحميل base.js ولا تشغيل Deno
        # 2. player_skip يتخطى تحميل صفحة الويب وملفات الكونفج والبيانات الأولية
        # 3. skip يتخطى تحميل ملفات m3u8 و dash والترجمات
        # 4. formats: missing_pot يسمح بصيغ m4a/webm السريعة بدون انتظار PO Token
        self.fast_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": None,
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "geo_bypass": False,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 5,
            "retries": 2,
            "extractor_args": {
                "youtube": {
                    "player_client": ["visionos", "android"],
                    "player_skip": ["webpage", "configs", "initial_data", "js"],
                    "skip": ["hls", "dash", "translated_subs"],
                    "formats": ["missing_pot"],
                    "fetch_pot": ["never"],
                }
            },
        }

        # إعدادات احتياطية للفيديوهات المقيدة أو المخصصة للأطفال (Made for Kids)
        self.fallback_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": None,
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "noplaylist": True,
            "skip_download": True,
            "js_runtimes": {"deno": {}, "node": {}},
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv_samsung", "web_embedded"],
                    "player_skip": ["webpage", "initial_data"],
                    "skip": ["translated_subs"],
                    "fetch_pot": ["never"],
                }
            },
        }

    def _extract_vid(self, link: str, videoid: str | bool | None = None) -> str:
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        if len(vid) == 11:
            return vid
        if "v=" in link:
            try:
                extracted = link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    return extracted
            except IndexError:
                pass
        elif "youtu.be/" in link:
            try:
                extracted = link.split("youtu.be/")[1].split("?")[0]
                if len(extracted) == 11:
                    return extracted
            except IndexError:
                pass
        return vid

    async def valid(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        return bool(re.match(self.regex, url))

    async def url(self, message: Any) -> str | None:
        if not message:
            return None
        msgs = [message]
        if getattr(message, "reply_to_message", None):
            msgs.append(message.reply_to_message)

        for msg in msgs:
            text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
            entities = (getattr(msg, "entities", None) or []) + (getattr(msg, "caption_entities", None) or [])
            for ent in entities:
                try:
                    if ent.type == enums.MessageEntityType.URL:
                        return text[ent.offset : ent.offset + ent.length].split("&si")[0]
                    if ent.url:
                        return ent.url.split("&si")[0]
                except Exception as e:
                    log.warning(f"Error parsing entity: {e}")
        return None

    async def _extract_native(self, query: str, opts: dict) -> dict:
        loop = asyncio.get_running_loop()

        def extract():
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(query, download=False)

        return await loop.run_in_executor(self._pool, extract)

    async def track(self, link: str, videoid: str | bool | None = None) -> tuple[dict[str, Any], str]:
        vid = self._extract_vid(link, videoid)
        query = f"https://www.youtube.com/watch?v={vid}" if len(vid) == 11 else link

        try:
            search = VideosSearch(query, limit=1)
            result = await search.next()

            if result and "result" in result and len(result["result"]) > 0:
                info = result["result"][0]
                v_id = info.get("id", vid)
                duration = info.get("duration") or "0:00"

                thumb_url = ""
                if "thumbnails" in info and len(info["thumbnails"]) > 0:
                    thumb_url = info["thumbnails"][-1].get("url", "")
                    if "?" in thumb_url:
                        thumb_url = thumb_url.split("?")[0]

                return {
                    "title": info.get("title", "Unknown"),
                    "link": f"https://www.youtube.com/watch?v={v_id}",
                    "vidid": v_id,
                    "duration_min": duration,
                    "thumb": thumb_url,
                }, v_id
            return {"title": "Unknown", "duration_min": "0:00", "thumb": "", "vidid": vid, "link": link}, vid
        except Exception as e:
            log.error(f"Track Search error: {e}")
            return {"title": "Unknown", "duration_min": "0:00", "thumb": "", "vidid": vid, "link": link}, vid

    async def details(self, link: str, videoid: str | bool | None = None) -> tuple[str, str, int, str, str]:
        data, vid = await self.track(link, videoid)
        dur = data.get("duration_min") or "0:00"

        secs = 0
        try:
            parts = [int(p) for p in str(dur).split(":") if str(p).isdigit()]
            if parts:
                secs = sum(p * (60 ** i) for i, p in enumerate(reversed(parts)))
        except Exception as e:
            log.error(f"Duration parsing error: {e}")

        return data.get("title", "Unknown"), str(dur), secs, data.get("thumb", ""), str(vid)

    async def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        try:
            search_obj = VideosSearch(query, limit=limit)
            result = await search_obj.next()

            if not result or "result" not in result:
                return []

            return [
                {
                    "title": d.get("title", "Unknown"),
                    "vidid": d.get("id"),
                    "duration": d.get("duration") or "0:00",
                }
                for d in result["result"]
            ]
        except Exception as e:
            log.error(f"Search error: {e}")
            return []

    async def download(
        self,
        link: str,
        mystic: Any = None,
        video: str | bool | None = None,
        videoid: str | bool | None = None,
        **kwargs,
    ) -> str | None:
        vid = self._extract_vid(link, videoid)
        target_url = f"https://www.youtube.com/watch?v={vid}" if len(vid) == 11 else link
        is_video = bool(video and str(video) not in ["False", "None", "0"])

        # فحص الكاش أولاً (استجابة فورية 0.001s)
        cache_key = f"{vid or target_url}:{'v' if is_video else 'a'}"
        now = time.time()
        cached = self._url_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

        media_format = (
            "best[ext=mp4][height<=720]/best[height<=720]/best"
            if is_video
            else "bestaudio[ext=m4a]/bestaudio/best"
        )

        # المحاولة الأولى: استخراج مباشر فائق السرعة (< 0.8s) عبر visionos/android بدون webpage أو JS
        opts = {**self.fast_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return direct_url
        except Exception as e:
            log.debug(f"Fast extraction fallback triggered for {target_url}: {e}")

        # المحاولة الاحتياطية: للفيديوهات المخصصة للأطفال أو المقيدة
        fb_opts = {**self.fallback_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, fb_opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return direct_url
        except Exception as e:
            log.error(f"Extraction Error: {e}")

        return None

    async def get_direct_link(self, link: str, *, prefer_audio: bool = True) -> str | None:
        return await self.download(link, None, video=not prefer_audio)

    async def get_playlist(self, url: str) -> list[str]:
        opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
            "force_ipv4": True,
            "extractor_args": {
                "youtubetab": {"skip": ["webpage"]}
            },
        }
        try:
            info = await self._extract_native(url, opts)
            return [
                f"https://www.youtube.com/watch?v={entry['id']}"
                for entry in info.get("entries", [])
                if entry and entry.get("id")
            ]
        except Exception as e:
            log.error(f"Playlist extraction error: {e}")
            return []

    async def download_thumb(self, thumbnail_url: str) -> str | None:
        if not thumbnail_url:
            return None
        os.makedirs("downloads", exist_ok=True)
        path = f"downloads/thumb_{int(time.time())}.jpg"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail_url) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(path, "wb") as f:
                            await f.write(await resp.read())
                        return path
        except Exception as e:
            log.error(f"Thumbnail download error: {e}")
        return None

    async def video(self, link: str, is_live: bool = False) -> tuple[int, str]:
        try:
            url = await self.get_direct_link(link, prefer_audio=True)
            if url:
                return 1, url
            return 0, ""
        except Exception as e:
            log.error(f"Live Video extraction error: {e}")
            return 0, ""


YouTube = YouTubeAPI()
