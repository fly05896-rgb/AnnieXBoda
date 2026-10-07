import asyncio
import re
import logging
import os
import time
from typing import Any

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
        self.base_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": None, # ضع مسار ملف الكوكيز هنا إذا لزم الأمر
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "concurrent_fragment_downloads": 10, # عدد آمن لتسريع التحميل بدون حظر
            "js_runtimes": {"deno": {}}, # تفعيل دينو كبيئة التشغيل الافتراضية الأسرع
            "remote_components": ["ejs:github"],
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv_embedded", "android", "ios"]
                }
            }
        }

    async def valid(self, url: str) -> bool:
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
        def extract():
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(query, download=False)
        return await asyncio.to_thread(extract)

    async def track(self, link: str, videoid: str | bool | None = None) -> tuple[dict[str, Any], str]:
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False"] else ""
        if not vid and "v=" in link:
            try:
                vid = link.split("v=")[1].split("&")[0]
            except IndexError:
                pass
        
        query = f"https://youtube.com/watch?v={vid}" if vid else link

        try:
            search = VideosSearch(query, limit=1)
            result = await search.next()
            
            if result and "result" in result and len(result["result"]) > 0:
                info = result["result"][0]
                v_id = info.get("id", vid)
                duration = info.get("duration", "0:00")
                
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

    async def details(self, link: str, videoid: str | bool | None = None) -> tuple[str, str | None, int, str, str]:
        data, vid = await self.track(link, videoid)
        dur = data.get("duration_min", "0:00")
        
        secs = 0
        try:
            parts = [int(p) for p in str(dur).split(":")]
            secs = sum(p * (60 ** i) for i, p in enumerate(reversed(parts)))
        except Exception as e:
            log.error(f"Duration parsing error: {e}")
            
        return data["title"], dur, secs, data["thumb"], str(vid)

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
                    "duration": d.get("duration", "0:00")
                }
                for d in result["result"]
            ]
        except Exception as e:
            log.error(f"Search error: {e}")
            return []

    async def download(self, link: str, mystic: Any, video: str | bool | None = None, videoid: str | bool | None = None, **kwargs) -> str | None:
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False"] else ""
        if not vid and "v=" in link:
            try:
                vid = link.split("v=")[1].split("&")[0]
            except IndexError:
                pass
        
        target_url = f"https://www.youtube.com/watch?v={vid}" if vid else link
        
        # اختيار الجودة الأفضل لبث تيليجرام
        # الصوت: الأولوية لـ m4a لتقليل استهلاك المعالج وتجنب الـ Transcoding
        # الفيديو: دمج أفضل فيديو بحد أقصى 1080p مع الصوت لتجنب مشاكل التشغيل
        media_format = "best[height<=1080]/best" if video else "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
        
        opts = self.base_opts.copy()
        opts["format"] = media_format
        opts["noplaylist"] = True
        
        try:
            info = await self._extract_native(target_url, opts)
            return info.get("url")
        except Exception as e:
            log.error(f"Extraction Error: {e}")
            return None

    async def get_direct_link(self, link: str, *, prefer_audio: bool = True) -> str | None:
        return await self.download(link, None, video=not prefer_audio)
                
    async def get_playlist(self, url: str) -> list[str]:
        opts = {
            "extract_flat": True,
            "quiet": True,
            "skip_download": True,
            "no_warnings": True
        }
        try:
            info = await self._extract_native(url, opts)
            return [f"https://www.youtube.com/watch?v={entry['id']}" for entry in info.get("entries", []) if entry.get("id")]
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
