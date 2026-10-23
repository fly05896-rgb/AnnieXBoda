# Authored By Certified Coders © 2026
# System: Apple Music API (Merged Shared Session & Fast YouTube Innertube Engine)

import re
from typing import List, Optional, Union
from bs4 import BeautifulSoup

from AnnieXMedia.platforms.Youtube import YouTube, WEB_CHROME_UA


class AppleAPI:
    def __init__(self):
        self.regex = r"^https:\/\/music\.apple\.com\/.+"
        self.base = "https://music.apple.com/in/playlist/"

    async def valid(self, link: str) -> bool:
        return bool(re.search(self.regex, link or ""))

    async def track(self, url: str, playid: Union[bool, str] = None):
        if playid:
            url = self.base + str(url)

        try:
            session = await YouTube._get_session()
            async with session.get(url, headers={"User-Agent": WEB_CHROME_UA}) as response:
                if response.status != 200:
                    return None, None
                html = await response.text()
        except Exception:
            return None, None

        soup = BeautifulSoup(html, "html.parser")
        title_query: Optional[str] = None

        for tag in soup.find_all("meta"):
            if tag.get("property") == "og:title":
                title_query = tag.get("content")
                break

        if not title_query and soup.title and soup.title.string:
            title_query = soup.title.string.replace("on Apple Music", "").strip()

        if not title_query:
            return None, None

        # تنظيف العنوان من جملة on Apple Music لزيادة دقة البحث
        title_query = re.sub(r"\s*on Apple Music.*$", "", title_query, flags=re.I).strip()

        try:
            details, vidid = await YouTube.track(title_query)
            if not details or not vidid or details.get("title") == "Unknown":
                return None, None
            return details, vidid
        except Exception:
            return None, None

    async def playlist(self, url: str, playid: Union[bool, str] = None):
        if playid:
            url = self.base + str(url)

        try:
            playlist_id = url.split("playlist/")[1]
        except Exception:
            playlist_id = "Unknown"

        try:
            session = await YouTube._get_session()
            async with session.get(url, headers={"User-Agent": WEB_CHROME_UA}) as response:
                if response.status != 200:
                    return [], None
                html = await response.text()
        except Exception:
            return [], None

        soup = BeautifulSoup(html, "html.parser")
        applelinks = soup.find_all("meta", attrs={"property": "music:song"})
        results: List[str] = []

        for item in applelinks:
            try:
                content = item.get("content", "")
                if "album/" in content:
                    slug = content.split("album/")[1].split("/")[0]
                else:
                    slug = content.rstrip("/").split("/")[-2]
                clean_name = slug.replace("-", " ").strip()
                if clean_name:
                    results.append(clean_name)
            except Exception:
                continue

        return results, playlist_id
