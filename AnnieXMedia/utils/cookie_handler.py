# Authored By Certified Coders © 2026
# System: Cookie Handler (100% Disabled / Cookie-Free Mode)

from contextlib import suppress
from pathlib import Path

COOKIE_PATH = None


def resolve_raw_cookie_url(url: str) -> str:
    return ""


async def fetch_and_store_cookies() -> None:
    """
    تم تعطيل نظام الكوكيز بالكامل بناءً على طلب المطور:
    يقوم بحذف أي ملف cookies.txt قديم إن وُجد لضمان عدم قراءته أبداً من قِبل أي مكتبة.
    """
    with suppress(Exception):
        legacy_path = Path("AnnieXMedia/assets/cookies.txt")
        if legacy_path.exists():
            legacy_path.unlink()
    return None
