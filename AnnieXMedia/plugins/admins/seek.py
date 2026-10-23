# Authored By Certified Coders © 2026
# Module: Seek Stream - Optimized for PyTgCalls v3.0 & Fast Direct Link Cache

from pyrogram import filters
from pyrogram.types import Message

from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.decorators import AdminRightsCheck
from AnnieXMedia.utils.formatters import seconds_to_min
from AnnieXMedia.utils.inline import close_markup
from config import BANNED_USERS


@app.on_message(
    filters.command(["seek", "cseek", "seekback", "cseekback", "مرر", "قدم", "رجع"], prefixes=["", "/", "!", "."])
    & filters.group
    & ~BANNED_USERS
)
@AdminRightsCheck
async def seek_comm(cli, message: Message, _, chat_id):
    if len(message.command) == 1:
        return await message.reply_text(_["admin_20"])

    query = message.text.split(None, 1)[1].strip()

    playing = db.get(chat_id)
    if not playing:
        return await message.reply_text(_["queue_2"])

    duration_seconds = int(playing[0]["seconds"])
    if duration_seconds == 0:
        return await message.reply_text(_["admin_22"])

    file_path = playing[0]["file"]
    duration_played = int(playing[0]["played"])
    duration = playing[0]["dur"]

    command = message.command[0]

    if not query.isnumeric():
        return await message.reply_text(_["admin_21"])

    duration_to_skip = int(query)

    if command in ["seekback", "cseekback", "رجع"]:
        if (duration_played - duration_to_skip) <= 10:
            return await message.reply_text(
                text=_["admin_23"].format(seconds_to_min(duration_played), duration),
                reply_markup=close_markup(_),
            )
        to_seek = duration_played - duration_to_skip
        is_back = True

    else:
        if (duration_seconds - (duration_played + duration_to_skip)) <= 10:
            return await message.reply_text(
                text=_["admin_23"].format(seconds_to_min(duration_played), duration),
                reply_markup=close_markup(_),
            )
        to_seek = duration_played + duration_to_skip
        is_back = False

    mystic = await message.reply_text(_["admin_24"])

    streamtype = playing[0]["streamtype"]
    mode = "video" if streamtype == "video" else "audio"

    if "vid_" in file_path:
        direct = await YouTube.get_direct_link(
            playing[0]["vidid"],
            prefer_audio=(mode != "video"),
        )
        if not direct:
            return await mystic.edit_text(_["admin_22"])
        file_path = direct

    check = (playing[0]).get("speed_path")
    if check:
        file_path = check
    if "index_" in file_path:
        file_path = playing[0]["vidid"]

    try:
        await StreamController.seek_stream(
            chat_id,
            file_path,
            seconds_to_min(to_seek),
            duration,
            mode,
        )
    except Exception:
        return await mystic.edit_text(_["admin_26"], reply_markup=close_markup(_))

    if is_back:
        db[chat_id][0]["played"] -= duration_to_skip
    else:
        db[chat_id][0]["played"] += duration_to_skip

    await mystic.edit_text(
        text=_["admin_25"].format(seconds_to_min(to_seek), message.from_user.mention),
        reply_markup=close_markup(_),
    )
