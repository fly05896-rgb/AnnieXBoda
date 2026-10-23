# Authored By Certified Coders © 2026
# System: Ultra-Fast YouTube Engine (yt-dlp 2026.10.21 + Innertube Multi-Client + orjson)

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

from AnnieXMedia.utils.cookie_handler import COOKIE_PATH

log = logging.getLogger("AnnieXMedia.YouTube")

# ── تفعيل الكاش الدائم في الرامات لـ yt-dlp 2026.10.21 ──
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


VISIONOS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)
TV_SAMSUNG_UA = (
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 2.4.0) "
    "AppleWebKit/538.1 (KHTML, like Gecko) Version/2.4.0 TV Safari/538.1"
)
WEB_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _get_valid_cookiefile() -> str | None:
    try:
        if COOKIE_PATH and os.path.exists(COOKIE_PATH) and os.path.getsize(COOKIE_PATH) > 100:
            return str(COOKIE_PATH)
    except Exception:
        pass
    return None


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
        self._cache_ttl = 3600
        self._http_session: aiohttp.ClientSession | None = None

        cache_dir = "/dev/shm/ytdlp_cache" if os.path.exists("/dev/shm") else "/tmp/ytdlp_cache"

        # 🚀 عملاء yt-dlp 2026.10.21 الجدد:
        # تم استبعاد android و android_vr و ios لأنها تطلب PO Token أو معطلة في 2026.10.21
        # وتم اعتماد visionos (بدون JS وبدون PO Token) + tv_samsung (العميل الافتراضي الأول الجديد في 2026.10.21)
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
            "cachedir": cache_dir,
            "extractor_args": {
                "youtube": {
                    "player_client": ["visionos", "tv_samsung"],
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
            "cookiefile": _get_valid_cookiefile(),
            "force_ipv4": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 8,
            "retries": 2,
            "cachedir": cache_dir,
            "js_runtimes": {"deno": {}, "node": {}},
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv_samsung", "tv_downgraded", "web_embedded"],
                    "player_skip": ["webpage", "initial_data"],
                    "skip": ["translated_subs"],
                    "formats": ["missing_pot"],
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
            timeout = aiohttp.ClientTimeout(total=5, connect=2)
            self._http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._http_session

    def get_stream_headers(self, url: str = "") -> dict[str, str]:
        """تجهيز الهيدرز المتوافقة مع روابط googlevideo لمنع HTTP 403 في FFmpeg/NTgCalls"""
        if "c=TVHTML5" in str(url):
            return {"User-Agent": TV_SAMSUNG_UA}
        return {"User-Agent": VISIONOS_UA}

    def invalidate_cache(self, vid: str) -> None:
        """مسح كاش الفيديو في حال انتهاء صلاحية الرابط المباشر"""
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

    async def _request_innertube_client(
        self,
        vid: str,
        client_payload: dict[str, Any],
        client_id: str,
        client_ver: str,
        user_agent: str,
        third_party_embed: bool = False,
    ) -> dict[str, Any] | None:
        context: dict[str, Any] = {"client": client_payload}
        if third_party_embed:
            context["thirdParty"] = {"embedUrl": "https://www.reddit.com/"}

        payload = {
            "context": context,
            "videoId": vid,
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
                return _json_loads(raw_bytes)
        except Exception as e:
            log.debug(f"Innertube client {client_payload.get('clientName')} error: {e}")
            return None

    async def _innertube_player(self, vid: str) -> dict[str, Any] | None:
        """
        دمج جلب بيانات الفيديو + استخراج وتخزين أفضل رابط صوت وفيديو وحجم الملف
        في طلب واحد فقط (~75ms) باستخدام orjson + Multi-Client Innertube Fallback
        """
        now = time.time()
        cached = self._meta_cache.get(vid)
        if cached and cached[1] > now and (f"{vid}:a" in self._url_cache or f"{vid}:v" in self._url_cache):
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
            # 1. العميل الأساسي الأسرع: VISIONOS 1.02 (يعطي روابط مباشرة صالحة بدون JS وبدون PO Token)
            data = await self._request_innertube_client(
                vid,
                client_payload={
                    "clientName": "VISIONOS",
                    "clientVersion": "1.02",
                    "deviceMake": "Apple",
                    "deviceModel": "RealityDevice17,1",
                    "osName": "visionOS",
                    "osVersion": "26.5.23O471",
                    "hl": "en",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                },
                client_id="101",
                client_ver="1.02",
                user_agent=VISIONOS_UA,
            )

            sd = (data or {}).get("streamingData") or {}
            vd = (data or {}).get("videoDetails") or {}

            # 2. إذا كان الفيديو مقيداً أو للأطفال (Made for Kids) أو بث مباشر بدون روابط في VISIONOS،
            # ننتقل فوراً في نفس اللحظة إلى عميل IOS (للبثوث المباشرة HLS) أو WEB_EMBEDDED (لتخطي القيود)
            has_direct_urls = any(
                f.get("url") for f in (sd.get("formats", []) + sd.get("adaptiveFormats", []))
            ) or bool(sd.get("hlsManifestUrl"))

            if not has_direct_urls or not vd.get("title"):
                ios_data = await self._request_innertube_client(
                    vid,
                    client_payload={
                        "clientName": "IOS",
                        "clientVersion": "21.26.4",
                        "deviceMake": "Apple",
                        "deviceModel": "iPhone16,2",
                        "osName": "iPhone",
                        "osVersion": "18.3.2.22D82",
                        "hl": "en",
                        "timeZone": "UTC",
                        "utcOffsetMinutes": 0,
                    },
                    client_id="5",
                    client_ver="21.26.4",
                    user_agent="com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
                )
                if ios_data and (ios_data.get("videoDetails", {}).get("title") or ios_data.get("streamingData")):
                    data = ios_data
                    sd = data.get("streamingData") or {}
                    vd = data.get("videoDetails") or vd

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

            best_audio_url: str | None = None
            best_audio_score: int = -1
            best_audio_fmt: dict[str, Any] = {}

            best_video_url: str | None = None
            best_video_score: int = -1
            best_video_fmt: dict[str, Any] = {}

            for fmt in sd.get("formats", []) + sd.get("adaptiveFormats", []):
                f_url = fmt.get("url")
                if not f_url:
                    continue
                itag = str(fmt.get("itag", ""))
                mime = str(fmt.get("mimeType", ""))
                bitrate = int(fmt.get("averageBitrate") or fmt.get("bitrate") or 0)
                height = int(fmt.get("height") or 0)

                # صيغ الفيديو المدمجة بصوت (مثل itag 18 = 360p mp4 أو itag 22 = 720p mp4)
                if itag in ("18", "22") or ("video" in mime and fmt.get("audioQuality")):
                    score = (1000 if itag == "22" else 800 if itag == "18" else height) + (bitrate // 1000)
                    if score > best_video_score:
                        best_video_score = score
                        best_video_url = f_url
                        best_video_fmt = fmt

                # صيغ الصوت النقية (تفضيل itag 140 m4a ثم 251 opus ثم الأعلى جودة)
                if "audio" in mime:
                    score = (
                        2000 if itag == "140"
                        else 1800 if itag == "251"
                        else 1500 if "mp4a" in mime
                        else 1000
                    ) + (bitrate // 1000)
                    if score > best_audio_score:
                        best_audio_score = score
                        best_audio_url = f_url
                        best_audio_fmt = fmt

            hls_url = sd.get("hlsManifestUrl")
            if is_live and hls_url:
                best_video_url = best_video_url or hls_url
                best_audio_url = best_audio_url or hls_url

            if not best_audio_url and best_video_url:
                best_audio_url = best_video_url
                best_audio_fmt = best_video_fmt
            if not best_video_url and hls_url:
                best_video_url = hls_url

            thumb_url = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
            ua_headers = self.get_stream_headers(best_audio_url or best_video_url or "")

            if best_audio_url:
                self._url_cache[f"{v_id}:a"] = (best_audio_url, now + self._cache_ttl)
                a_clen = int(best_audio_fmt.get("contentLength") or 0)
                if not a_clen and "clen=" in best_audio_url:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(best_audio_url).query)
                        a_clen = int(qs.get("clen", [0])[0])
                    except Exception:
                        a_clen = 0
                a_mime = str(best_audio_fmt.get("mimeType", ""))
                a_ext = "m4a" if ("mp4" in a_mime or str(best_audio_fmt.get("itag")) == "140") else "webm"
                self._stream_info_cache[f"{v_id}:a"] = (
                    {
                        "vidid": v_id,
                        "title": title,
                        "duration_sec": secs,
                        "duration_min": duration_min,
                        "thumbnail": thumb_url,
                        "direct_url": best_audio_url,
                        "filesize": a_clen,
                        "vcodec": "none" if "audio" in a_mime else "avc1",
                        "ext": a_ext,
                        "headers": ua_headers,
                        "can_direct_pump": bool(a_clen > 0 and "m3u8" not in best_audio_url),
                    },
                    now + self._cache_ttl,
                )

            if best_video_url:
                self._url_cache[f"{v_id}:v"] = (best_video_url, now + self._cache_ttl)
                v_clen = int(best_video_fmt.get("contentLength") or 0)
                if not v_clen and "clen=" in best_video_url:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(best_video_url).query)
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
                        "direct_url": best_video_url,
                        "filesize": v_clen,
                        "vcodec": "avc1",
                        "ext": "mp4",
                        "headers": ua_headers,
                        "can_direct_pump": bool(v_clen > 0 and "m3u8" not in best_video_url),
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
        """بحث يوتيوب مباشر عبر IPv4 + orjson في 80ms مع استباق جلب الرابط المباشر للأغنية الأولى"""
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

            # 🚀 الدمج الاستباقي (Pre-warm): تجهيز الرابط المباشر لأول نتيجة في الخلفية فوراً!
            if prefetch_first and results:
                first_vid = results[0]["vidid"]
                if f"{first_vid}:a" not in self._url_cache:
                    asyncio.create_task(self._innertube_player(first_vid))

            return results
        except Exception as e:
            log.error(f"Fast Innertube Search error: {e}")
            return []

    async def extract_fast_stream_info(self, query_or_link: str, is_video: bool = False) -> dict[str, Any] | None:
        """
        دالة مدمجة لمحرك SongDownloader (أمر يوت / ابعتلي):
        تستخرج الرابط المباشر + الحجم بالبايت + التفاصيل عبر Innertube في ~80ms بدون تشغيل yt-dlp!
        """
        s = query_or_link.strip()
        vid = self._extract_vid(s, s)
        if len(vid) != 11:
            results = await self._innertube_search(s, limit=1, prefetch_first=False)
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

        # 1. إذا كان لدينا ID الفيديو (11 حرفاً)، نجلبه مع الرابط المباشر في طلب واحد (~75ms)
        if len(vid) == 11:
            info = await self._innertube_player(vid)
            if info:
                return info, vid

        # 2. البحث المباشر السريع عبر IPv4 (مع إطلاق استباق الرابط المباشر في الخلفية)
        query = vid if len(vid) == 11 else str(link).strip()
        results = await self._innertube_search(query, limit=1, prefetch_first=True)
        if results:
            first = results[0]
            v_id = first["vidid"]
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
        """استرجاع سريع لبيانات الفيديو من الكاش أو Innertube لتوليد الصور المصغرة فوراً"""
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
        # استباق جلب الرابط المباشر للعنصر المعروض على السلايدر
        if item.get("vidid"):
            asyncio.create_task(self._innertube_player(item["vidid"]))
        return item["title"], str(item["duration_min"]), item["thumb"], item["vidid"]

    async def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        results = await self._innertube_search(query, limit=limit, prefetch_first=True)
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

        # 1. مسار Innertube الفوري المدمج (~75ms أو انتظار الـ Future الجارية من البحث)
        if len(vid) == 11:
            await self._innertube_player(vid)
            cached = self._url_cache.get(cache_key)
            if cached and cached[1] > now:
                return (cached[0], True) if wants_tuple else cached[0]

        media_format = (
            str(format_id)
            if format_id
            else (
                "18/22/best[ext=mp4][height<=720]/best[height<=720]/best"
                if is_video
                else "140/251/bestaudio[ext=m4a]/bestaudio/best"
            )
        )

        # 2. مسار yt-dlp السريع (visionos + tv_samsung بدون JS)
        opts = {**self.fast_opts, "format": media_format}
        try:
            info = await self._extract_native(target_url, opts)
            direct_url = info.get("url") if info else None
            if direct_url:
                self._url_cache[cache_key] = (direct_url, now + self._cache_ttl)
                return (direct_url, True) if wants_tuple else direct_url
        except Exception as e:
            log.debug(f"Fast extraction fallback triggered for {target_url}: {e}")

        # 3. مسار yt-dlp الاحتياطي (tv_samsung + tv_downgraded + web_embedded مع الكوكيز)
        fb_opts = {
            **self.fallback_opts,
            "cookiefile": _get_valid_cookiefile(),
            "format": media_format,
        }
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
