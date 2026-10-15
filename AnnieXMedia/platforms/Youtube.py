import asyncio
import logging
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiofiles
import aiohttp
from pyrogram import enums
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
        self._meta_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._cache_ttl = 3600
        self._http_session: aiohttp.ClientSession | None = None

        self.fast_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": None,
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "geo_bypass": False,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 5,
            "retries": 2,
            "cachedir": "/tmp/ytdlp_cache",
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

        self.fallback_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": None,
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "noplaylist": True,
            "skip_download": True,
            "cachedir": "/tmp/ytdlp_cache",
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

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            # إجبار IPv4 (AF_INET) لمنع خطأ Failed to make request وتعليق البحث نهائياً
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET,
                limit=100,
                ttl_dns_cache=600,
                ssl=False,
            )
            timeout = aiohttp.ClientTimeout(total=4, connect=2)
            self._http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._http_session

    def _extract_vid(self, link: str, videoid: str | bool | None = None) -> str:
        if videoid is True and len(str(link)) == 11:
            return str(link)
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        if vid.endswith("_v"):
            vid = vid[:-2]
        if len(vid) == 11:
            return vid
        s_link = str(link).strip()
        if len(s_link) == 11 and not s_link.startswith("http") and " " not in s_link:
            return s_link
        if "v=" in s_link:
            try:
                extracted = s_link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    return extracted
            except IndexError:
                pass
        for prefix in ("youtu.be/", "/shorts/", "/live/"):
            if prefix in s_link:
                try:
                    extracted = s_link.split(prefix)[1].split("?")[0].split("&")[0]
                    if len(extracted) == 11:
                        return extracted
                except IndexError:
                    pass
        return vid

    @staticmethod
    def _sec_to_min(seconds: int) -> str:
        if not seconds or seconds <= 0:
            return "0:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

    async def _innertube_player(self, vid: str) -> dict[str, Any] | None:
        """جلب بيانات الفيديو من نقطة player مباشرة عبر IPv4 في 80ms"""
        now = time.time()
        cached = self._meta_cache.get(vid)
        if cached and cached[1] > now:
            return cached[0]

        payload = {
            "context": {
                "client": {
                    "clientName": "VISIONOS",
                    "clientVersion": "1.02",
                    "deviceMake": "Apple",
                    "deviceModel": "RealityDevice17,1",
                    "osName": "visionOS",
                    "osVersion": "26.5.23O471",
                    "hl": "en",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                }
            },
            "videoId": vid,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
            "X-YouTube-Client-Name": "101",
            "X-YouTube-Client-Version": "1.02",
            "Origin": "https://www.youtube.com",
        }
        try:
            session = await self._get_session()
            async with session.post(
                "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            vd = data.get("videoDetails") or {}
            v_id = vd.get("videoId") or vid
            title = vd.get("title")
            if not title:
                return None

            is_live = bool(vd.get("isLive") or vd.get("isLiveContent") and int(vd.get("lengthSeconds") or 0) == 0)
            secs = int(vd.get("lengthSeconds") or 0)
            duration_min = "Live" if is_live else self._sec_to_min(secs)

            # حفظ الرابط المباشر في الكاش تلقائياً أثناء جلب التفاصيل لتوفير طلب كامل لاحقاً!
            sd = data.get("streamingData") or {}
            for fmt in sd.get("formats", []) + sd.get("adaptiveFormats", []):
                if f_url := fmt.get("url"):
                    itag = str(fmt.get("itag", ""))
                    if itag == "18":
                        self._url_cache[f"{v_id}:v"] = (f_url, now + self._cache_ttl)
                        self._url_cache.setdefault(f"{v_id}:a", (f_url, now + self._cache_ttl))
                    elif "audio" in str(fmt.get("mimeType", "")):
                        self._url_cache[f"{v_id}:a"] = (f_url, now + self._cache_ttl)

            info = {
                "title": title,
                "link": f"{self.base}{v_id}",
                "vidid": v_id,
                "duration_min": duration_min,
                "thumb": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
            }
            self._meta_cache[v_id] = (info, now + self._cache_ttl)
            return info
        except Exception as e:
            log.debug(f"Innertube player fast lookup error: {e}")
            return None

    async def _innertube_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """بحث يوتيوب مباشر عبر IPv4 في 100ms بدون مكتبة youtubesearchpython المعطلة"""
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20260708.00.00",
                    "hl": "ar",
                    "gl": "EG",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                }
            },
            "query": query,
            "params": "EgIQAfABAQ==",  # فلتر الفيديوهات فقط
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": "2.20260708.00.00",
            "Origin": "https://www.youtube.com",
        }
        try:
            session = await self._get_session()
            async with session.post(
                "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

            results = []
            sections = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            now = time.time()
            for sec in sections:
                items = sec.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    vr = item.get("videoRenderer")
                    if not vr:
                        continue
                    v_id = vr.get("videoId")
                    if not v_id:
                        continue
                    title_runs = vr.get("title", {}).get("runs", [])
                    title = "".join(r.get("text", "") for r in title_runs) or "Unknown"
                    dur_text = (
                        vr.get("lengthText", {}).get("simpleText")
                        or vr.get("lengthText", {}).get("accessibility", {})
                        .get("accessibilityData", {})
                        .get("label")
                        or "Live"
                    )
                    thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
                    entry = {
                        "title": title,
                        "link": f"{self.base}{v_id}",
                        "vidid": v_id,
                        "id": v_id,
                        "duration": dur_text,
                        "duration_min": dur_text,
                        "thumb": thumb,
                    }
                    self._meta_cache[v_id] = (entry, now + self._cache_ttl)
                    results.append(entry)
                    if len(results) >= limit:
                        return results
            return results
        except Exception as e:
            log.error(f"Fast Innertube Search error: {e}")
            return []

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

        # 1. إذا كان لدينا ID الفيديو (11 حرفاً)، نجلبه في 80ms عبر Innertube Player مباشرة
        if len(vid) == 11:
            info = await self._innertube_player(vid)
            if info:
                return info, vid

        # 2. البحث المباشر السريع عبر IPv4
        query = vid if len(vid) == 11 else str(link).strip()
        results = await self._innertube_search(query, limit=1)
        if results:
            first = results[0]
            v_id = first["vidid"]
            return {
                "title": first["title"],
                "link": first["link"],
                "vidid": v_id,
                "duration_min": first["duration_min"],
                "thumb": first["thumb"],
            }, v_id

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
        results = await self._innertube_search(link, limit=10)
        if not results:
            return "Unknown", "0:00", "", ""
        idx = query_type if 0 <= query_type < len(results) else 0
        item = results[idx]
        return item["title"], str(item["duration_min"]), item["thumb"], item["vidid"]

    async def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        results = await self._innertube_search(query, limit=limit)
        return [
            {
                "title": d.get("title", "Unknown"),
                "vidid": d.get("vidid", ""),
                "duration": d.get("duration") or "0:00",
            }
            for d in results
        ]

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
            "noprogress": True,
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
        wants_tuple = bool(songaudio or songvideo)

        cache_key = f"{vid or target_url}:{'v' if is_video else 'a'}"
        now = time.time()
        cached = self._url_cache.get(cache_key)
        if cached and cached[1] > now:
            return (cached[0], True) if wants_tuple else cached[0]

        # محاولة سريعة جداً عبر Innertube Player (80ms)
        if len(vid) == 11:
            await self._innertube_player(vid)
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

        opts = {**self.fast_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.debug(f"Fast extraction fallback triggered for {target_url}: {e}")

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
        path = f"downloads/thumb_{int(time.time() * 1000)}.jpg"
        try:
            session = await self._get_session()
            async with session.get(thumbnail_url) as resp:
                if resp.status == 200:
                    async with aiofiles.open(path, "wb") as f:
                        await f.write(await resp.read())
                    return path
        except Exception as e:
            log.error(f"Thumbnail download error: {e}")
        return None

    async def video(self, link: str, videoid: bool | str | None = None) -> tuple[int, str]:
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
