# Authored By Certified Coders © 2026
# System: Resso API (Shared Session & Fast YouTube Innertube Engine)

import re
from typing import Union
from bs4 import BeautifulSoup

from AnnieXMedia.platforms.Youtube import YouTube, WEB_CHROME_UA


class RessoAPI:
    def __init__(self):
        self.regex = r"^(https:\/\/m\.resso\.com\/)(.*)$"
        self.base = "https://m.resso.com/"

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
        title = None
        des = ""
        for tag in soup.find_all("meta"):
            if tag.get("property") == "og:title":
                title = tag.get("content")
            elif tag.get("property") == "og:description":
                des = tag.get("content") or ""
                with_split = des.split("·")
                if with_split:
                    des = with_split[0].strip()

        query = f"{title or ''} {des}".strip()
        if not query:
            return None, None

        try:
            details, vidid = await YouTube.track(query)
            if not details or not vidid or details.get("title") == "Unknown":
                return None, None
            return details, vidid
        except Exception:
            return None, None
