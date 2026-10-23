# Authored By Certified Coders © 2026
# Module: Thumbnail Generator (Glass Design - Instant Cache & Shared Session)
# Optimized for Python 3.13 & Pillow 11.x (Zero VideosSearch Overhead)

import asyncio
import os
import aiofiles
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from config import YOUTUBE_IMG_URL
from AnnieXMedia.core.dir import CACHE_DIR
from AnnieXMedia.platforms.Youtube import YouTube

# Arabic Support
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None

# ================= CONSTANTS =================
PANEL_W, PANEL_H = 763, 545
PANEL_X = (1280 - PANEL_W) // 2
PANEL_Y = 88
TRANSPARENCY = 7
INNER_OFFSET = 36
THUMB_W, THUMB_H = 542, 273
THUMB_X = PANEL_X + (PANEL_W - THUMB_W) // 2
THUMB_Y = PANEL_Y + INNER_OFFSET
TITLE_X = 377
META_X = 377
TITLE_Y = THUMB_Y + THUMB_H + 10
META_Y = TITLE_Y + 45
BAR_X, BAR_Y = 388, META_Y + 45
BAR_RED_LEN = 280
BAR_TOTAL_LEN = 480
ICONS_W, ICONS_H = 415, 45
ICONS_X = PANEL_X + (PANEL_W - ICONS_W) // 2
ICONS_Y = BAR_Y + 48
MAX_TITLE_WIDTH = 580
# =============================================


def fix_ar(text):
    if not text:
        return ""
    if arabic_reshaper and get_display:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            return text
    return text


def trim_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """Uses getlength which is compatible with Pillow 10+ & Python 3.13"""
    ellipsis = "…"
    try:
        if font.getlength(text) <= max_w:
            return text
        for i in range(len(text) - 1, 0, -1):
            if font.getlength(text[:i] + ellipsis) <= max_w:
                return text[:i] + ellipsis
    except AttributeError:
        if font.getsize(text)[0] <= max_w:
            return text
        for i in range(len(text) - 1, 0, -1):
            if font.getsize(text[:i] + ellipsis)[0] <= max_w:
                return text[:i] + ellipsis
    return ellipsis


def _render_glass_thumb_sync(
    thumb_path: str,
    cache_path: str,
    title: str,
    views: str,
    duration_text: str,
    is_live: bool,
) -> str:
    base = Image.open(thumb_path).convert("RGBA")
    base = base.resize((1280, 720), Image.Resampling.LANCZOS)

    # 1. Background
    bg = base.filter(ImageFilter.BoxBlur(5))
    bg = ImageEnhance.Brightness(bg).enhance(0.6)

    # 2. Glass Panel
    crop = bg.crop((PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H))
    crop = crop.filter(ImageFilter.GaussianBlur(30))
    crop = ImageEnhance.Color(crop).enhance(1.5)
    crop = ImageEnhance.Brightness(crop).enhance(1.1)

    tint = Image.new("RGBA", crop.size, (255, 255, 255, TRANSPARENCY))
    glass_panel = Image.alpha_composite(crop, tint)

    mask = Image.new("L", (PANEL_W, PANEL_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, PANEL_W, PANEL_H), radius=40, fill=255)

    bg.paste(glass_panel, (PANEL_X, PANEL_Y), mask)

    # 3. Border
    draw = ImageDraw.Draw(bg)
    draw.rounded_rectangle(
        (PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H),
        radius=40,
        outline=(255, 255, 255, 90),
        width=2,
    )

    # Fonts
    try:
        title_font = ImageFont.truetype("AnnieXMedia/assets/thumb/font2.ttf", 32)
        regular_font = ImageFont.truetype("AnnieXMedia/assets/thumb/font.ttf", 18)
    except Exception:
        title_font = regular_font = ImageFont.load_default()

    # 4. Inner Thumbnail
    thumb_inner = base.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    tmask = Image.new("L", thumb_inner.size, 0)
    ImageDraw.Draw(tmask).rounded_rectangle((0, 0, THUMB_W, THUMB_H), radius=20, fill=255)
    bg.paste(thumb_inner, (THUMB_X, THUMB_Y), tmask)

    # 5. Text (Arabic Fixed)
    final_title = fix_ar(trim_to_width(title, title_font, MAX_TITLE_WIDTH))
    final_views = fix_ar(f"YouTube | {views}")

    draw.text((TITLE_X, TITLE_Y), final_title, fill="black", font=title_font)
    draw.text((META_X, META_Y), final_views, fill="black", font=regular_font)

    # 6. Progress Bar
    draw.line([(BAR_X, BAR_Y), (BAR_X + BAR_RED_LEN, BAR_Y)], fill="#FF0000", width=6)
    draw.line([(BAR_X + BAR_RED_LEN, BAR_Y), (BAR_X + BAR_TOTAL_LEN, BAR_Y)], fill="#AAAAAA", width=5)
    draw.ellipse([(BAR_X + BAR_RED_LEN - 7, BAR_Y - 7), (BAR_X + BAR_RED_LEN + 7, BAR_Y + 7)], fill="#FF0000")

    draw.text((BAR_X, BAR_Y + 15), "00:00", fill="black", font=regular_font)
    end_color = "#FF0000" if is_live else "black"
    draw.text(
        (BAR_X + BAR_TOTAL_LEN - (80 if is_live else 60), BAR_Y + 15),
        duration_text,
        fill=end_color,
        font=regular_font,
    )

    # 7. Icons
    icons_path = "AnnieXMedia/assets/thumb/play_icons.png"
    if os.path.isfile(icons_path):
        ic = Image.open(icons_path).convert("RGBA")
        ic = ic.resize((ICONS_W, ICONS_H), Image.Resampling.LANCZOS)
        r, g, b, a = ic.split()
        black_ic = Image.merge("RGBA", (r.point(lambda _: 0), g.point(lambda _: 0), b.point(lambda _: 0), a))
        bg.paste(black_ic, (ICONS_X, ICONS_Y), black_ic)

    bg.save(cache_path)
    return cache_path


async def get_thumb(videoid: str) -> str:
    if not videoid:
        return YOUTUBE_IMG_URL

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{videoid}_v7_glass_py313.png")
    if os.path.exists(cache_path):
        return cache_path

    # 🚀 استرجاع البيانات من كاش YouTube الفوري بدون استدعاء VideosSearch البطيئة!
    meta = await YouTube.get_cached_meta(videoid)
    if meta:
        title = meta.get("title", "Unknown Track")
        thumbnail = (meta.get("thumb") or f"https://i.ytimg.com/vi/{videoid}/hqdefault.jpg").split("?")[0]
        duration = meta.get("duration_min") or meta.get("duration") or "00:00"
        views = meta.get("views", "YouTube")
    else:
        title = "Unknown Track"
        thumbnail = f"https://i.ytimg.com/vi/{videoid}/hqdefault.jpg"
        duration = "00:00"
        views = "YouTube"

    is_live = not duration or str(duration).strip().lower() in {"", "live", "live now"}
    duration_text = "Live" if is_live else duration or "00:00"

    thumb_path = os.path.join(CACHE_DIR, f"temp_{videoid}.png")
    try:
        session = await YouTube._get_session()
        async with session.get(thumbnail) as resp:
            if resp.status == 200:
                async with aiofiles.open(thumb_path, "wb") as f:
                    await f.write(await resp.read())
            else:
                return YOUTUBE_IMG_URL
    except Exception:
        return YOUTUBE_IMG_URL

    try:
        # تشغيل معالجة الصور الثقيلة (Blur/Resize) في Thread منفصل لعدم تجميد الـ Event Loop
        return await asyncio.to_thread(
            _render_glass_thumb_sync,
            thumb_path,
            cache_path,
            title,
            views,
            duration_text,
            is_live,
        )
    except Exception as e:
        print(f"Thumb Gen Error: {e}")
        return YOUTUBE_IMG_URL
    finally:
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass
