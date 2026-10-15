import asyncio
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiofiles
import aiohttp
from pyrogram import enums
from youtubesearchpython.aio import VideosSearch
from yt_dlp import YoutubeDL

log = logging.getLogger("AnnieXMedia.YouTube")


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|live/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+|OLAK5uy_[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self._pool = ThreadPoolExecutor(max_workers=16)
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 3600

        # إعدادات الاستخراج الفوري (< 1s) المتوافقة مع yt-dlp 2026.08.19
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

        # إعدادات احتياطية للفيديوهات المقيدة أو المخصصة للأطفال
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
        if videoid is True and len(str(link)) == 11:
            return str(link)
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        if vid.endswith("_v"):
            vid = vid[:-2]
        if len(vid) == 11:
            return vid
        if len(str(link)) == 11 and not str(link).startswith("http"):
            return str(link)
        if "v=" in link:
            try:
                extracted = link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    return extracted
            except IndexError:
                pass
        for prefix in ("youtu.be/", "/shorts/", "/live/"):
            if prefix in link:
                try:
                    extracted = link.split(prefix)[1].split("?")[0].split("&")[0]
                    if len(extracted) == 11:
                        return extracted
                except IndexError:
                    pass
        return vid

    async def valid(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        return bool(re.search(self.regex, url))

    async def exists(self, link: str, videoid: bool | str | None = None) -> bool:
        if videoid:
            link = self.base + str(link)
        return await self.valid(link)

    async def url(self, message: Any) -> str | None:
        if not message:
            return None
        msgs = [message]
        if getattr(message, "reply_to_message", None):
            msgs.append(message.reply_to_message)

        for msg in msgs:
            text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
            entities = (getattr(msg, "entities", None) or []) + (
                getattr(msg, "caption_entities", None) or []
            )
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
        query = f"{self.base}{vid}" if len(vid) == 11 else link

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
                    "link": f"{self.base}{v_id}",
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
                secs = sum(p * (60**i) for i, p in enumerate(reversed(parts)))
        except Exception as e:
            log.error(f"Duration parsing error: {e}")

        return data.get("title", "Unknown"), str(dur), secs, data.get("thumb", ""), str(vid)

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: str | bool | None = None,
    ) -> tuple[str, str, str, str]:
        if videoid:
            link = self.base + str(link)
        if "&" in link:
            link = link.split("&")[0]
        try:
            a = VideosSearch(link, limit=10)
            result = (await a.next()).get("result", [])
            if not result:
                return "Unknown", "0:00", "", ""
            idx = query_type if 0 <= query_type < len(result) else 0
            item = result[idx]
            title = item.get("title", "Unknown")
            duration_min = item.get("duration") or "0:00"
            vidid = item.get("id", "")
            thumbnail = item["thumbnails"][0]["url"].split("?")[0] if item.get("thumbnails") else ""
            return title, str(duration_min), thumbnail, vidid
        except Exception as e:
            log.error(f"Slider error: {e}")
            return "Unknown", "0:00", "", ""

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

    async def playlist(
        self,
        link: str,
        limit: int = 25,
        user_id: int | None = None,
        videoid: str | bool | None = None,
    ) -> list[str]:
        if videoid:
            link = self.listbase + str(videoid) if isinstance(videoid, str) else self.listbase + str(link)
        if "&" in link and "list=" in link:
            list_id = link.split("list=")[1].split("&")[0]
            link = f"{self.listbase}{list_id}"

        opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
            "force_ipv4": True,
            "playlistend": int(limit) if limit else 25,
            "extractor_args": {"youtubetab": {"skip": ["webpage"]}},
        }
        try:
            info = await self._extract_native(link, opts)
            entries = info.get("entries", []) if info else []
            return [entry["id"] for entry in entries if entry and entry.get("id")][:limit]
        except Exception as e:
            log.error(f"Playlist extraction error: {e}")
            return []

    async def get_playlist(self, url: str) -> list[str]:
        ids = await self.playlist(url, limit=50)
        return [f"{self.base}{vid}" for vid in ids]

    async def download(
        self,
        link: str,
        mystic: Any = None,
        video: str | bool | None = None,
        videoid: str | bool | None = None,
        songaudio: str | bool | None = None,
        songvideo: str | bool | None = None,
        format_id: str | bool | None = None,
        title: str | None = None,
        **kwargs,
    ) -> Any:
        vid = self._extract_vid(link, videoid)
        target_url = f"{self.base}{vid}" if len(vid) == 11 else link
        is_video = bool((video or songvideo) and str(video) not in ["False", "None", "0"])

        # التوافق مع Spotify.py و Soundcloud.py التي تتوقع (path, direct) عند تمرير songaudio/songvideo
        wants_tuple = bool(songaudio or songvideo)

        cache_key = f"{vid or target_url}:{'v' if is_video else 'a'}"
        now = time.time()
        cached = self._url_cache.get(cache_key)
        if cached and cached[1] > now:
            return (cached[0], True) if wants_tuple else cached[0]

        media_format = (
            str(format_id)
            if format_id
            else (
                "best[ext=mp4][height<=720]/best[height<=720]/best"
                if is_video
                else "bestaudio[ext=m4a]/bestaudio/best"
            )
        )

        # المحاولة الأولى: استخراج مباشر فائق السرعة (< 1 ثانية)
        opts = {**self.fast_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.debug(f"Fast extraction fallback triggered for {target_url}: {e}")

        # المحاولة الاحتياطية
        fb_opts = {**self.fallback_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, fb_opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.error(f"Extraction Error: {e}")

        return (None, False) if wants_tuple else None

    async def get_direct_link(self, link: str, *, prefer_audio: bool = True) -> str | None:
        return await self.download(link, None, video=not prefer_audio)

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

    async def video(self, link: str, videoid: bool | str | None = None) -> tuple[int, str]:
        """
        يُستخدم في التقديم والتأخير (Seek) والبث المباشر:
        يجلب رابطاً يحتوي على فيديو وصوت معاً ليقبل التشغيل في وضعي الصوت والفيديو بدون خطأ NoVideoSourceFound
        """
        try:
            vid = self._extract_vid(link, videoid)
            target = f"{self.base}{vid}" if len(vid) == 11 else link
            url = await self.download(target, None, video=True, videoid=vid)
            if url:
                return 1, url
            return 0, ""
        except Exception as e:
            log.error(f"Video extraction error: {e}")
            return 0, ""


YouTube = YouTubeAPI()
