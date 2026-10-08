import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import aiofiles
import aiohttp
from pyrogram import enums
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

    # 🚀 جلب تفاصيل التراك بسرعة باستخدام VideosSearch
    async def track(self, link: str, videoid: str | bool | None = None) -> tuple[dict[str, Any], str]:
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        if not vid and "v=" in link:
            try:
                extracted = link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    vid = extracted
            except IndexError:
                pass
        
        query = f"https://www.youtube.com/watch?v={vid}" if vid else link

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
                    "duration": d.get("duration") or "0:00"
                }
                for d in result["result"]
            ]
        except Exception as e:
            log.error(f"Search error: {e}")
            return []

    # 🚀 المعالج الصاروخي والمضمون (Subprocess Engine)
    async def _run_ytdlp_cli(self, target_url: str, is_video: bool) -> str | None:
        """
        يستخدم yt-dlp كأمر موجه (CLI) لاستخراج الرابط المباشر.
        هذه الطريقة تفعل تلقائياً أنظمة (PO Tokens) و (JS Challenge) الحديثة بدون فشل.
        """
        # تحديد الجودة (الفيديو: دمج بأفضل 720p، الصوت: أفضل صوت فقط)
        format_str = "bestvideo[height<=720]+bestaudio/best[height<=720]/best" if is_video else "bestaudio/best"
        
        # الأوامر الأساسية: استخراج JSON فقط (بدون تحميل)، وتخطي تحذيرات الشهادات.
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--no-check-certificate",
            "--geo-bypass",
            "--format", format_str,
            "--dump-json",
            target_url
        ]

        # إضافة ملف الكوكيز إن وجد لتخطي القيود العمرية
        if os.path.isfile("cookies.txt"):
            cmd.extend(["--cookies", "cookies.txt"])

        try:
            # تشغيل العملية في الخلفية (Asynchronous Subprocess)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                err_msg = stderr.decode('utf-8').strip()
                log.error(f"yt-dlp CLI error for {target_url}: {err_msg}")
                return None

            try:
                # فك تشفير مخرجات JSON
                info = json.loads(stdout.decode('utf-8'))
                
                # إرجاع الرابط المباشر
                direct_url = info.get("url")
                
                # محاولة احتياطية إذا لم يتوفر الرابط في الجذر
                if not direct_url and "formats" in info:
                    for f in info["formats"]:
                        if f.get("url") and (f.get("vcodec") != "none" if is_video else f.get("acodec") != "none"):
                            direct_url = f.get("url")
                            break
                            
                return direct_url
            except json.JSONDecodeError:
                log.error(f"Failed to parse yt-dlp JSON output for {target_url}")
                return None

        except Exception as e:
            log.error(f"Failed to execute yt-dlp subprocess: {e}")
            return None

    # قلب عملية التشغيل (استخراج الـ URL المباشر للفيديو/الصوت)
    async def download(self, link: str, mystic: Any, video: str | bool | None = None, videoid: str | bool | None = None, **kwargs) -> str | None:
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        link = str(link) if link else ""
        
        if not vid and "v=" in link:
            try:
                extracted = link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    vid = extracted
            except IndexError:
                pass
        
        target_url = ""
        if vid and len(vid) >= 11:
            clean_vid = vid.replace("_v", "")[:11] 
            target_url = f"https://www.youtube.com/watch?v={clean_vid}"
        elif link and link.startswith("http"):
            target_url = link
            
        if not target_url:
            log.error(f"Download Error: Invalid URL/ID. link='{link}', videoid='{videoid}'")
            return None
        
        # استدعاء المحرك الصاروخي 🚀
        is_video = bool(video)
        return await self._run_ytdlp_cli(target_url, is_video)

    # دالة مساعدة للحصول على الرابط المباشر
    async def get_direct_link(self, link: str, *, prefer_audio: bool = True) -> str | None:
        return await self.download(link, None, video=not prefer_audio)
                
    async def get_playlist(self, url: str) -> list[str]:
        # استخراج بيانات القائمة باستخدام CLI أيضاً لضمان الدقة
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            url
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            
            if process.returncode != 0:
                return []
                
            entries = []
            for line in stdout.decode('utf-8').splitlines():
                try:
                    info = json.loads(line)
                    if info.get("id"):
                        entries.append(f"https://www.youtube.com/watch?v={info['id']}")
                except json.JSONDecodeError:
                    continue
            return entries
        except Exception as e:
            log.error(f"Playlist extraction error: {e}")
            return []

    # تحميل الصورة المصغرة (Thumbnail)
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
            url = await self.get_direct_link(link, prefer_audio=not is_live)
            if url:
                return 1, url
            return 0, ""
        except Exception as e:
            log.error(f"Live Video extraction error: {e}")
            return 0, ""

YouTube = YouTubeAPI()
