# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/platforms/Youtube.py
# System: Ultra-Fast Cookie-Free YouTube Engine (VISIONOS itag=140 + ANDROID itag=18 Parallel + yt-dlp 2026.10.21)
# Mode: 100% COOKIE-FREE (cookiefile: None) — Benchmarked on Fly.io (~50ms Extract)

import asyncio
import json
import logging
import os
import re
import socket
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiofiles
import aiohttp
from pyrogram import enums
from yt_dlp import YoutubeDL

try:
    import orjson

    def _json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)

    def _json_loads(data: bytes | str) -> Any:
        return orjson.loads(data)
except ImportError:
    def _json_dumps(obj: Any) -> bytes:
        return json.dumps(obj).encode("utf-8")

    def _json_loads(data: bytes | str) -> Any:
        return json.loads(data)

log = logging.getLogger("AnnieXMedia.YouTube")

_SHARED_CODE_CACHE: dict = {}
_SHARED_PLAYER_CACHE: dict = {}

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


class _SilentYTDLPLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        log.debug(f"yt-dlp internal: {msg}")


VISIONOS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)
ANDROID_UA = "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip"
WEB_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


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
        self._stream_info_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._inflight_player: dict[str, asyncio.Future] = {}
        self._visitor_data: str | None = None
        self._visitor_data_ts: float = 0.0
        self._cache_ttl = 3600
        self._http_session: aiohttp.ClientSession | None = None

        cache_dir = "/dev/shm/ytdlp_cache" if os.path.exists("/dev/shm") else "/tmp/ytdlp_cache"

        # =========================================================================
        # إعدادات yt-dlp 2026.10.21 الأسرع على سيرفر Fly.io (174ms) بدون أي كوكيز:
        # نستخدم فقط ["android", "visionos"] مع تخطي webpage, configs, initial_data, js
        # =========================================================================
        self.fast_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": None,
            "logger": _SilentYTDLPLogger(),
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "geo_bypass": False,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 5,
            "retries": 1,
            "cachedir": cache_dir,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "visionos"],
                    "player_skip": ["webpage", "configs", "initial_data", "js"],
                    "skip": ["dash", "translated_subs"],
                    "fetch_pot": ["never"],
                }
            },
        }

        self.fallback_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cookiefile": None,
            "logger": _SilentYTDLPLogger(),
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 6,
            "retries": 2,
            "cachedir": cache_dir,
            "extractor_args": {
                "youtube": {
                    "player_client": ["visionos", "android"],
                    "player_skip": ["webpage", "initial_data"],
                    "skip": ["translated_subs"],
                    "fetch_pot": ["never"],
                }
            },
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET,
                limit=128,
                limit_per_host=64,
                ttl_dns_cache=1800,
                use_dns_cache=True,
                keepalive_timeout=120,
                enable_cleanup_closed=True,
                ssl=False,
            )
            timeout = aiohttp.ClientTimeout(total=4, connect=1.5)
            self._http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._http_session

    def get_stream_headers(self, url: str = "") -> dict[str, str]:
        u = str(url)
        if "c=ANDROID" in u:
            return {"User-Agent": ANDROID_UA}
        return {"User-Agent": VISIONOS_UA}

    def invalidate_cache(self, vid: str) -> None:
        vid = self._extract_vid(vid, vid)
        for suffix in ("a", "v"):
            self._url_cache.pop(f"{vid}:{suffix}", None)
            self._stream_info_cache.pop(f"{vid}:{suffix}", None)

    def _extract_vid(self, link: str, videoid: str | bool | None = None) -> str:
        if videoid is True and len(str(link)) == 11:
            return str(link)
        vid = str(videoid) if videoid and str(videoid) not in ["True", "False", "None"] else ""
        if vid.endswith("_v"):
            vid = vid[:-2]
        if len(vid) == 11:
            return vid
        s_link = str(link).strip()
        if s_link.endswith("_v") and len(s_link) == 13:
            return s_link[:-2]
        if len(s_link) == 11 and not s_link.startswith("http") and " " not in s_link:
            return s_link
        if "v=" in s_link:
            try:
                extracted = s_link.split("v=")[1].split("&")[0]
                if len(extracted) == 11:
                    return extracted
            except IndexError:
                pass
        for prefix in ("youtu.be/", "/shorts/", "/live/", "/embed/", "/v/"):
            if prefix in s_link:
                try:
                    extracted = s_link.split(prefix)[1].split("?")[0].split("&")[0].split("/")[0]
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

    async def _ensure_visitor_data(self) -> str | None:
        now = time.time()
        if self._visitor_data and (now - self._visitor_data_ts) < 3600:
            return self._visitor_data
        try:
            session = await self._get_session()
            payload = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": "2.20260708.00.00",
                        "hl": "ar",
                        "gl": "EG",
                    }
                }
            }
            headers = {
                "Content-Type": "application/json",
                "User-Agent": WEB_CHROME_UA,
                "Origin": "https://www.youtube.com",
            }
            async with session.post(
                "https://www.youtube.com/youtubei/v1/guide?prettyPrint=false",
                data=_json_dumps(payload),
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = _json_loads(await resp.read())
                    v_data = (data.get("responseContext") or {}).get("visitorData")
                    if v_data:
                        self._visitor_data = str(v_data)
                        self._visitor_data_ts = now
        except Exception:
            pass
        return self._visitor_data

    async def _request_innertube_client(
        self,
        vid: str,
        client_payload: dict[str, Any],
        client_id: str,
        client_ver: str,
        user_agent: str,
    ) -> dict[str, Any] | None:
        client_copy = dict(client_payload)
        if self._visitor_data:
            client_copy.setdefault("visitorData", self._visitor_data)

        payload = {
            "context": {"client": client_copy},
            "videoId": vid,
            "playbackContext": {
                "contentPlaybackContext": {
                    "html5Preference": "HTML5_PREF_WANTS",
                }
            },
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "X-YouTube-Client-Name": client_id,
            "X-YouTube-Client-Version": client_ver,
            "Origin": "https://www.youtube.com",
        }
        if self._visitor_data:
            headers["X-Goog-Visitor-Id"] = self._visitor_data

        try:
            session = await self._get_session()
            async with session.post(
                "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                data=_json_dumps(payload),
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    return None
                raw_bytes = await resp.read()
                data = _json_loads(raw_bytes)
                v_data = (data.get("responseContext") or {}).get("visitorData")
                if v_data and not self._visitor_data:
                    self._visitor_data = str(v_data)
                    self._visitor_data_ts = time.time()
                return data
        except Exception as e:
            log.debug(f"Innertube client {client_payload.get('clientName')} error: {e}")
            return None

    async def _innertube_player(self, vid: str) -> dict[str, Any] | None:
        """
        استخراج فوري بالتوازي (VISIONOS + ANDROID) في ~55ms بدون كوكيز:
        - VISIONOS يعطي itag=140 (صوت M4A نقي فائق السرعة 154ms في FFmpeg) + HLS للبث المباشر
        - ANDROID يعطي itag=18 (فيديو 360p مدمج بالصوت) للفيديو وكبديل فوري للصوت
        """
        now = time.time()
        cached = self._meta_cache.get(vid)
        if cached and cached[1] > now and f"{vid}:a" in self._url_cache and f"{vid}:v" in self._url_cache:
            return cached[0]

        if inflight := self._inflight_player.get(vid):
            try:
                return await inflight
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight_player[vid] = fut

        try:
            if not self._visitor_data:
                await self._ensure_visitor_data()

            vision_task = self._request_innertube_client(
                vid,
                client_payload={
                    "clientName": "VISIONOS",
                    "clientVersion": "1.02",
                    "deviceMake": "Apple",
                    "deviceModel": "RealityDevice17,1",
                    "osName": "visionOS",
                    "osVersion": "26.5.23O471",
                    "hl": "ar",
                    "gl": "EG",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                },
                client_id="101",
                client_ver="1.02",
                user_agent=VISIONOS_UA,
            )

            android_task = self._request_innertube_client(
                vid,
                client_payload={
                    "clientName": "ANDROID",
                    "clientVersion": "21.26.364",
                    "androidSdkVersion": 30,
                    "osName": "Android",
                    "osVersion": "11",
                    "hl": "ar",
                    "gl": "EG",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                },
                client_id="3",
                client_ver="21.26.364",
                user_agent=ANDROID_UA,
            )

            vision_data, android_data = await asyncio.gather(vision_task, android_task)

            vd = (
                (vision_data or {}).get("videoDetails")
                or (android_data or {}).get("videoDetails")
                or {}
            )
            if not vd.get("title"):
                vd = (android_data or {}).get("videoDetails") or {}
            if not vd.get("title"):
                fut.set_result(None)
                return None

            v_id = vd.get("videoId") or vid
            title = vd.get("title")
            is_live = bool(
                vd.get("isLive")
                or (vd.get("isLiveContent") and int(vd.get("lengthSeconds") or 0) == 0)
            )
            secs = int(vd.get("lengthSeconds") or 0)
            duration_min = "Live" if is_live else self._sec_to_min(secs)
            views_count = vd.get("viewCount")
            views_str = f"{int(views_count):,} Views" if str(views_count).isdigit() else "YouTube"
            channel_name = vd.get("author") or "YouTube"
            thumb_url = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

            vision_sd = (vision_data or {}).get("streamingData") or {}
            android_sd = (android_data or {}).get("streamingData") or {}

            # 1. استخراج رابط الصوت النقي itag=140 من VISIONOS مع اختيار المسار الأصلي (منع الدبلجة الإنجليزية التلقائية)
            vision_audio_url: str | None = None
            vision_audio_fmt: dict[str, Any] = {}
            best_audio_score = -999
            for fmt in vision_sd.get("adaptiveFormats", []):
                f_url = fmt.get("url")
                if not f_url:
                    continue
                mime = str(fmt.get("mimeType", ""))
                itag = str(fmt.get("itag", ""))
                if "audio" in mime:
                    at = fmt.get("audioTrack") or {}
                    disp = str(at.get("displayName") or "").lower()
                    tid = str(at.get("id") or "").lower()
                    if "descriptive" in disp:
                        score = -100
                    elif at.get("isAutoDubbed") or "dubbed" in disp:
                        score = -50
                    elif not at or "original" in disp or "أصلي" in disp or tid.startswith("ar"):
                        score = 100
                    elif at.get("audioIsDefault"):
                        score = 40
                    else:
                        score = 0
                    if itag == "140":
                        score += 20
                    if score > best_audio_score:
                        best_audio_score = score
                        vision_audio_url = f_url
                        vision_audio_fmt = fmt

            # 2. استخراج رابط itag=18 من ANDROID (للفيديو وكبديل مضمون 100% للصوت)
            prog_stream_url: str | None = None
            prog_fmt: dict[str, Any] = {}
            for sd_source in (android_sd, vision_sd):
                for fmt in sd_source.get("formats", []):
                    f_url = fmt.get("url")
                    if not f_url:
                        continue
                    itag = str(fmt.get("itag", ""))
                    if itag in ("18", "22"):
                        prog_stream_url = f_url
                        prog_fmt = fmt
                        if itag == "18":
                            break
                if prog_stream_url:
                    break

            hls_url = vision_sd.get("hlsManifestUrl") or android_sd.get("hlsManifestUrl")

            if is_live and hls_url:
                audio_call_url = hls_url
                video_call_url = hls_url
            else:
                audio_call_url = vision_audio_url or prog_stream_url or hls_url
                video_call_url = prog_stream_url or hls_url or vision_audio_url

            if audio_call_url:
                self._url_cache[f"{v_id}:a"] = (audio_call_url, now + self._cache_ttl)
            if video_call_url:
                self._url_cache[f"{v_id}:v"] = (video_call_url, now + self._cache_ttl)

            dl_audio_url = vision_audio_url or prog_stream_url
            dl_audio_fmt = vision_audio_fmt if vision_audio_url else prog_fmt
            if dl_audio_url:
                a_clen = int(dl_audio_fmt.get("contentLength") or 0)
                if not a_clen and "clen=" in dl_audio_url:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(dl_audio_url).query)
                        a_clen = int(qs.get("clen", [0])[0])
                    except Exception:
                        a_clen = 0
                a_mime = str(dl_audio_fmt.get("mimeType", ""))
                is_pure_audio = "audio" in a_mime
                a_ext = "m4a" if ("mp4" in a_mime or str(dl_audio_fmt.get("itag")) == "140") else "webm"
                self._stream_info_cache[f"{v_id}:a"] = (
                    {
                        "vidid": v_id,
                        "title": title,
                        "duration_sec": secs,
                        "duration_min": duration_min,
                        "thumbnail": thumb_url,
                        "direct_url": dl_audio_url,
                        "filesize": a_clen,
                        "vcodec": "none" if is_pure_audio else "avc1",
                        "ext": a_ext if is_pure_audio else "mp4",
                        "headers": self.get_stream_headers(dl_audio_url),
                        "can_direct_pump": bool(is_pure_audio and a_clen > 0 and "m3u8" not in dl_audio_url),
                    },
                    now + self._cache_ttl,
                )

            if prog_stream_url:
                v_clen = int(prog_fmt.get("contentLength") or 0)
                if not v_clen and "clen=" in prog_stream_url:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(prog_stream_url).query)
                        v_clen = int(qs.get("clen", [0])[0])
                    except Exception:
                        v_clen = 0
                self._stream_info_cache[f"{v_id}:v"] = (
                    {
                        "vidid": v_id,
                        "title": title,
                        "duration_sec": secs,
                        "duration_min": duration_min,
                        "thumbnail": thumb_url,
                        "direct_url": prog_stream_url,
                        "filesize": v_clen,
                        "vcodec": "avc1",
                        "ext": "mp4",
                        "headers": self.get_stream_headers(prog_stream_url),
                        "can_direct_pump": bool(v_clen > 0 and "m3u8" not in prog_stream_url),
                    },
                    now + self._cache_ttl,
                )

            info = {
                "title": title,
                "link": f"{self.base}{v_id}",
                "vidid": v_id,
                "duration_min": duration_min,
                "duration_sec": secs,
                "thumb": thumb_url,
                "views": views_str,
                "channel": channel_name,
            }
            self._meta_cache[v_id] = (info, now + self._cache_ttl)
            fut.set_result(info)
            return info
        except Exception as e:
            log.debug(f"Innertube player fast lookup error: {e}")
            if not fut.done():
                fut.set_result(None)
            return None
        finally:
            self._inflight_player.pop(vid, None)

    async def _innertube_search(self, query: str, limit: int = 10, prefetch_first: bool = True) -> list[dict[str, Any]]:
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
            "params": "EgIQAfABAQ==",
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": WEB_CHROME_UA,
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": "2.20260708.00.00",
            "Origin": "https://www.youtube.com",
        }
        if self._visitor_data:
            headers["X-Goog-Visitor-Id"] = self._visitor_data

        try:
            session = await self._get_session()
            async with session.post(
                "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
                data=_json_dumps(payload),
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    return []
                raw_bytes = await resp.read()
                data = _json_loads(raw_bytes)

            v_data = (data.get("responseContext") or {}).get("visitorData")
            if v_data:
                self._visitor_data = str(v_data)
                self._visitor_data_ts = time.time()

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
                        or vr.get("lengthText", {})
                        .get("accessibility", {})
                        .get("accessibilityData", {})
                        .get("label")
                        or "Live"
                    )
                    views_text = (
                        vr.get("shortViewCountText", {}).get("simpleText")
                        or vr.get("viewCountText", {}).get("simpleText")
                        or "YouTube"
                    )
                    channel_runs = (
                        vr.get("ownerText", {}).get("runs")
                        or vr.get("longBylineText", {}).get("runs")
                        or []
                    )
                    channel_name = channel_runs[0].get("text", "YouTube") if channel_runs else "YouTube"
                    published_text = vr.get("publishedTimeText", {}).get("simpleText") or ""

                    thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
                    entry = {
                        "title": title,
                        "link": f"{self.base}{v_id}",
                        "vidid": v_id,
                        "id": v_id,
                        "duration": dur_text,
                        "duration_min": dur_text,
                        "thumb": thumb,
                        "views": views_text,
                        "channel": channel_name,
                        "published": published_text,
                    }
                    self._meta_cache[v_id] = (entry, now + self._cache_ttl)
                    results.append(entry)
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break

            if prefetch_first and results:
                first_vid = results[0]["vidid"]
                if f"{first_vid}:a" not in self._url_cache:
                    await self._innertube_player(first_vid)

            return results
        except Exception as e:
            log.error(f"Fast Innertube Search error: {e}")
            return []

    async def extract_fast_stream_info(self, query_or_link: str, is_video: bool = False) -> dict[str, Any] | None:
        s = query_or_link.strip()
        vid = self._extract_vid(s, s)
        if len(vid) != 11:
            results = await self._innertube_search(s, limit=1, prefetch_first=True)
            if not results:
                return None
            vid = results[0]["vidid"]

        suffix = "v" if is_video else "a"
        cache_key = f"{vid}:{suffix}"
        now = time.time()
        cached = self._stream_info_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

        await self._innertube_player(vid)
        cached = self._stream_info_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]
        return None

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

        if len(vid) == 11:
            info = await self._innertube_player(vid)
            if info:
                return info, vid

        query = vid if len(vid) == 11 else str(link).strip()
        results = await self._innertube_search(query, limit=1, prefetch_first=True)
        if results:
            first = results[0]
            v_id = first["vidid"]
            cached_meta = self._meta_cache.get(v_id)
            if cached_meta:
                return cached_meta[0], v_id
            return {
                "title": first["title"],
                "link": first["link"],
                "vidid": v_id,
                "duration_min": first["duration_min"],
                "thumb": first["thumb"],
                "views": first.get("views", "YouTube"),
                "channel": first.get("channel", "YouTube"),
            }, v_id

        return {"title": "Unknown", "duration_min": "0:00", "thumb": "", "vidid": vid, "link": link}, vid

    async def details(self, link: str, videoid: str | bool | None = None) -> tuple[str, str, int, str, str]:
        data, vid = await self.track(link, videoid)
        dur = data.get("duration_min") or "0:00"

        secs = int(data.get("duration_sec") or 0)
        if not secs:
            try:
                parts = [int(p) for p in str(dur).split(":") if str(p).isdigit()]
                if parts:
                    secs = sum(p * (60**i) for i, p in enumerate(reversed(parts)))
            except Exception as e:
                log.error(f"Duration parsing error: {e}")

        return data.get("title", "Unknown"), str(dur), secs, data.get("thumb", ""), str(vid)

    async def get_cached_meta(self, vid: str) -> dict[str, Any] | None:
        vid = self._extract_vid(vid, vid)
        if not vid:
            return None
        now = time.time()
        cached = self._meta_cache.get(vid)
        if cached and cached[1] > now:
            return cached[0]
        return await self._innertube_player(vid)

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
        results = await self._innertube_search(link, limit=10, prefetch_first=False)
        if not results:
            return "Unknown", "0:00", "", ""
        idx = query_type if 0 <= query_type < len(results) else 0
        item = results[idx]
        if item.get("vidid"):
            asyncio.create_task(self._innertube_player(item["vidid"]))
        return item["title"], str(item["duration_min"]), item["thumb"], item["vidid"]

    async def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        results = await self._innertube_search(query, limit=limit, prefetch_first=False)
        return [
            {
                "title": d.get("title", "Unknown"),
                "vidid": d.get("vidid", ""),
                "duration": d.get("duration") or "0:00",
                "thumb": d.get("thumb", ""),
                "views": d.get("views", "YouTube"),
                "channel": d.get("channel", "YouTube"),
                "published": d.get("published", ""),
                "link": d.get("link", ""),
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
            "cookiefile": None,
            "force_ipv4": True,
            "playlistend": int(limit) if limit else 25,
            "extractor_args": {"youtubetab": {"skip": ["webpage"]}},
        }
        try:
            info = await self._extract_native(link, opts)
            entries = info.get("entries", []) if info else []
            ids = [entry["id"] for entry in entries if entry and entry.get("id")][:limit]
            if ids:
                asyncio.create_task(self._innertube_player(ids[0]))
            return ids
        except Exception as e:
            log.error(f"Playlist extraction error: {e}")
            return []

    async def get_playlist(self, url: str) -> list[str]:
        ids = await self.playlist(url, limit=50)
        return [f"{self.base}{vid}" for vid in ids]

    async def _respect_available_at(self, info: dict | None) -> None:
        if not info:
            return
        avail = info.get("available_at") or 0
        if avail:
            wait_s = float(avail) - time.time()
            if 0 < wait_s <= 6.5:
                await asyncio.sleep(wait_s + 0.2)

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

        # 1. المسار الصاروخي عبر Innertube JSON (~50ms بدون كوكيز)
        if len(vid) == 11:
            await self._innertube_player(vid)
            cached = self._url_cache.get(cache_key)
            if cached and cached[1] > now:
                return (cached[0], True) if wants_tuple else cached[0]

        if format_id:
            media_format = str(format_id)
        elif is_video:
            media_format = "18/22/best[ext=mp4]/best[protocol*=m3u8]/best"
        else:
            media_format = "bestaudio[ext=m4a][language_preference>=10]/140/18/bestaudio[ext=m4a]/bestaudio/best"

        # 2. المسار الاحتياطي السريع عبر yt-dlp (android + visionos فقط — 174ms)
        opts = {**self.fast_opts, "cookiefile": None, "format": media_format}
        if self._visitor_data:
            yt_args = dict(opts["extractor_args"]["youtube"])
            yt_args["visitor_data"] = [self._visitor_data]
            opts["extractor_args"] = {"youtube": yt_args}

        try:
            info = await self._extract_native(target_url, opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, time.time() + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.debug(f"Primary yt-dlp fallback triggered for {target_url}: {e}")

        # 3. المسار الاحتياطي الثاني بدون كوكيز
        fb_opts = {**self.fallback_opts, "cookiefile": None, "format": media_format}
        try:
            info = await self._extract_native(target_url, fb_opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, time.time() + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.error(f"Cookie-free Extraction Error: {e}")

        return (None, False) if wants_tuple else None

    async def recover_stream(self, failed_link: str, videoid: str | None = None, video: bool = False) -> str | None:
        vid = self._extract_vid(failed_link, videoid)
        if not vid or len(vid) != 11:
            return None
        self.invalidate_cache(vid)

        # جلب رابط ANDROID itag=18 مباشرة في 70ms
        android_data = await self._request_innertube_client(
            vid,
            client_payload={
                "clientName": "ANDROID",
                "clientVersion": "21.26.364",
                "androidSdkVersion": 30,
                "osName": "Android",
                "osVersion": "11",
                "hl": "ar",
                "gl": "EG",
            },
            client_id="3",
            client_ver="21.26.364",
            user_agent=ANDROID_UA,
        )
        sd = (android_data or {}).get("streamingData") or {}
        for fmt in sd.get("formats", []):
            if fmt.get("url") and str(fmt.get("itag", "")) in ("18", "22"):
                direct_url = fmt["url"]
                now = time.time()
                self._url_cache[f"{vid}:a"] = (direct_url, now + self._cache_ttl)
                self._url_cache[f"{vid}:v"] = (direct_url, now + self._cache_ttl)
                return direct_url

        target_url = f"{self.base}{vid}"
        recovery_opts = {
            **self.fast_opts,
            "cookiefile": None,
            "format": "18/best[ext=mp4]/best",
        }
        try:
            info = await self._extract_native(target_url, recovery_opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                now = time.time()
                self._url_cache[f"{vid}:a"] = (direct_url, now + self._cache_ttl)
                self._url_cache[f"{vid}:v"] = (direct_url, now + self._cache_ttl)
                return direct_url
        except Exception as e:
            log.error(f"Stream recovery error for {vid}: {e}")
        return None

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
