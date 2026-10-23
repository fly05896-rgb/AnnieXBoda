# Authored By Certified Coders 2026
# File Path: AnnieXMedia/plugins/bot/inline.py
# Module: Inline Search - Powered by Fast YouTube Innertube Engine

from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultPhoto,
)

from AnnieXMedia import YouTube, app
from AnnieXMedia.utils.inlinequery import answer
from config import BANNED_USERS


@app.on_inline_query(~BANNED_USERS)
async def inline_query_handler(client, query):
    text = query.query.strip().lower()
    answers = []
    if text == "":
        try:
            await client.answer_inline_query(query.id, results=answer, cache_time=10)
        except Exception:
            return
    else:
        results = await YouTube.search(text, limit=15)
        for item in results[:15]:
            title = (item.get("title") or "Unknown").title()
            duration = item.get("duration") or "0:00"
            views = item.get("views") or "YouTube"
            thumbnail = (item.get("thumb") or "").split("?")[0]
            channel = item.get("channel") or "YouTube"
            link = item.get("link") or f"https://www.youtube.com/watch?v={item.get('vidid', '')}"
            published = item.get("published") or "YouTube"

            description = f"{views} | {duration} دقـيـقـة | {channel} | {published}"

            buttons = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="يـوتـيـوب",
                            url=link,
                        )
                    ],
                ]
            )

            searched_text = f"""
<b>الـعـنـوان :</b> <a href={link}>{title}</a>

<b>الـمـدة :</b> {duration} دقـيـقـة
<b>الـمـشـاهـدات :</b> <code>{views}</code>
<b>الـقـنـاة :</b> {channel}
<b>تـاريـخ الـنـشـر :</b> {published}


<u><b>➻ بـحـث الانـلايـن بـواسـطـة {app.name}</b></u>"""

            answers.append(
                InlineQueryResultPhoto(
                    photo_url=thumbnail,
                    title=title,
                    thumb_url=thumbnail,
                    description=description,
                    caption=searched_text,
                    reply_markup=buttons,
                )
            )
        try:
            return await client.answer_inline_query(query.id, results=answers)
        except Exception:
            return
