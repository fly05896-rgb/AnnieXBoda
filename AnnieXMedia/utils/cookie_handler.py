# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/utils/cookie_handler.py

from pathlib import Path
from urllib.parse import urlsplit
import aiohttp

from config import COOKIE_URL
from AnnieXMedia.utils.errors import capture_internal_err

COOKIE_PATH = Path("AnnieXMedia/assets/cookies.txt")


def _extract_paste_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else ""


def resolve_raw_cookie_url(url: str) -> str:
    url = (url or "").strip()
    low = url.lower()

    if "pastebin.com/" in low and "/raw/" not in low:
        paste_id = _extract_paste_id(url)
        return f"https://pastebin.com/raw/{paste_id}" if paste_id else url

    if "batbin.me/" in low:
        if "/api/v2/paste/" in low:
            return url
        paste_id = _extract_paste_id(url)
        return f"https://batbin.me/api/v2/paste/{paste_id}" if paste_id else url

    return url


@capture_internal_err
async def fetch_and_store_cookies():
    if not COOKIE_URL:
        raise EnvironmentError("⚠️ ᴄᴏᴏᴋɪᴇ_ᴜʀʟ ɴᴏᴛ sᴇᴛ ɪɴ ᴇɴᴠ.")

    raw_url = resolve_raw_cookie_url(COOKIE_URL)

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                raw_url,
                headers={"User-Agent": "annie-cookie-fetcher/2.0"},
            ) as response:
                if response.status != 200:
                    raise ConnectionError(f"HTTP {response.status}")
                cookies = (await response.text() or "").strip()
    except Exception as e:
        raise ConnectionError(f"⚠️ ᴄᴀɴ'ᴛ ꜰᴇᴛᴄʜ ᴄᴏᴏᴋɪᴇs:\n{e}")

    if not cookies.startswith("# Netscape"):
        raise ValueError("⚠️ ɪɴᴠᴀʟɪᴅ ᴄᴏᴏᴋɪᴇ ꜰᴏʀᴍᴀᴛ. ɴᴇᴇᴅs ɴᴇᴛsᴄᴀᴘᴇ ꜰᴏʀᴍᴀᴛ.")

    if len(cookies) < 100:
        raise ValueError("⚠️ ᴄᴏᴏᴋɪᴇ ᴄᴏɴᴛᴇɴᴛ ᴛᴏᴏ sʜᴏʀᴛ. ᴘᴏssɪʙʟʏ ɪɴᴠᴀʟɪᴅ.")

    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        COOKIE_PATH.write_text(cookies, encoding="utf-8")
    except Exception as e:
        raise IOError(f"⚠️ ғᴀɪʟᴇᴅ ᴛᴏ sᴀᴠᴇ ᴄᴏᴏᴋɪᴇs: {e}")
