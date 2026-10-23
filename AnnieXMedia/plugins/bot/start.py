# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/plugins/bot/start.py

import time
from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from AnnieXMedia import YouTube, app
from AnnieXMedia.misc import _boot_
from AnnieXMedia.plugins.sudo.sudoers import sudoers_list
from AnnieXMedia.utils.database import (
    add_served_chat,
    add_served_user,
    blacklisted_chats,
    get_lang,
    is_banned_user,
    is_on_off,
)
from AnnieXMedia.utils.decorators.language import LanguageStart
from AnnieXMedia.utils.formatters import get_readable_time
from AnnieXMedia.utils.inline.help import first_page as help_pannel
from AnnieXMedia.utils.inline.start import private_panel, start_panel
from config import BANNED_USERS
from strings import get_string

START_IMG_URL = getattr(config, "START_IMG_URL", "https://files.catbox.moe/ompn1o.jpg")
LOGGER_ID = getattr(config, "LOGGER_ID", config.OWNER_ID)


@app.on_message(filters.command(["start"]) & filters.private & ~BANNED_USERS)
@LanguageStart
async def start_pm(client, message: Message, _):
    await add_served_user(message.from_user.id)
    try:
        await message.react("❤")
    except Exception:
        pass

    if len(message.text.split()) > 1:
        name = message.text.split(None, 1)[1]
        if name[0:4] == "help":
            keyboard = help_pannel(_)
            return await message.reply_photo(
                photo=START_IMG_URL,
                caption=_["help_1"].format(config.SUPPORT_CHAT),
                reply_markup=keyboard,
            )

        if name[0:3] == "sud":
            await sudoers_list(client=client, message=message, _=_)
            if await is_on_off(2):
                if LOGGER_ID != 0:
                    return await app.send_message(
                        chat_id=LOGGER_ID,
                        text=(
                            f"{message.from_user.mention} قــام بـبـدء الـبـوت لـمـعـرفـة <b>قـائـمـة الـمـطـوريـن</b>.\n\n"
                            f"<b>آيــدي الـشـخـص :</b> <code>{message.from_user.id}</code>\n"
                            f"<b>الـيـوزر :</b> @{message.from_user.username}"
                        ),
                    )
            return

        if name[0:3] == "inf":
            m = await message.reply_text("🔎")
            vid = (str(name)).replace("info_", "", 1).strip()

            try:
                info, _ = await YouTube.track(vid, videoid=vid)
                title = info.get("title", "Unknown")
                duration = info.get("duration_min", "0:00")
                views = info.get("views", "YouTube")
                thumbnail = info.get("thumb") or START_IMG_URL
                channel = info.get("channel", "YouTube")
                link = info.get("link") or f"https://www.youtube.com/watch?v={vid}"
                channellink = "https://www.youtube.com"
                published = "YouTube"

                searched_text = _["start_6"].format(
                    title, duration, views, published, channellink, channel, app.mention
                )
                key = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(text=_["S_B_8"], url=link),
                            InlineKeyboardButton(text=_["S_B_9"], url=config.SUPPORT_CHAT),
                        ],
                    ]
                )
                await m.delete()
                await app.send_photo(
                    chat_id=message.chat.id,
                    photo=thumbnail,
                    caption=searched_text,
                    reply_markup=key,
                )
                if await is_on_off(2):
                    if LOGGER_ID != 0:
                        return await app.send_message(
                            chat_id=LOGGER_ID,
                            text=(
                                f"{message.from_user.mention} قــام بـبـدء الـبـوت لـمـعـرفـة <b>مـعـلـومـات الأغـنـيـة</b>.\n\n"
                                f"<b>آيــدي الـشـخـص :</b> <code>{message.from_user.id}</code>\n"
                                f"<b>الـيـوزر :</b> @{message.from_user.username}"
                            ),
                        )
            except Exception as e:
                await m.edit_text(f"Error: {e}")
                return
    else:
        out = private_panel(_)

        if client.me.photo:
            chat_photo = client.me.photo.big_file_id
        elif message.from_user.photo:
            chat_photo = message.from_user.photo.big_file_id
        else:
            chat_photo = START_IMG_URL

        try:
            await message.reply_photo(
                photo=chat_photo,
                caption=_["start_2"].format(message.from_user.mention, app.mention),
                reply_markup=InlineKeyboardMarkup(out),
            )
        except Exception:
            await message.reply_photo(
                photo=START_IMG_URL,
                caption=_["start_2"].format(message.from_user.mention, app.mention),
                reply_markup=InlineKeyboardMarkup(out),
            )

        if getattr(config, "LOG", True):
            if LOGGER_ID != 0:
                sender_id = message.from_user.id
                sender_name = message.from_user.first_name
                try:
                    await app.send_message(
                        LOGGER_ID,
                        f"{message.from_user.mention} قــام بـبـدء الـبـوت .. ⚡\n\n**آيــدي الـشـخـص :** {sender_id}\n**الاســم:** {sender_name}",
                    )
                except Exception:
                    pass


@app.on_message(filters.command(["start"]) & filters.group & ~BANNED_USERS)
@LanguageStart
async def start_gp(client, message: Message, _):
    out = start_panel(_)
    uptime = int(time.time() - _boot_)
    await message.reply_photo(
        photo=START_IMG_URL,
        caption=_["start_1"].format(app.mention, get_readable_time(uptime)),
        reply_markup=InlineKeyboardMarkup(out),
    )
    return await add_served_chat(message.chat.id)


@app.on_message(filters.new_chat_members, group=-1)
async def welcome(client, message: Message):
    for member in message.new_chat_members:
        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
            if await is_banned_user(member.id):
                try:
                    await message.chat.ban_member(member.id)
                except Exception:
                    pass
            if member.id == app.id:
                if message.chat.type != ChatType.SUPERGROUP:
                    await message.reply_text(_["start_4"])
                    return await app.leave_chat(message.chat.id)
                if message.chat.id in await blacklisted_chats():
                    await message.reply_text(
                        _["start_5"].format(
                            app.mention,
                            f"https://t.me/{app.username}?start=sudolist",
                            config.SUPPORT_CHAT,
                        ),
                        disable_web_page_preview=True,
                    )
                    return await app.leave_chat(message.chat.id)

                out = start_panel(_)
                await message.reply_photo(
                    photo=START_IMG_URL,
                    caption=_["start_3"].format(
                        message.from_user.first_name,
                        app.mention,
                        message.chat.title,
                        app.mention,
                    ),
                    reply_markup=InlineKeyboardMarkup(out),
                )
                await add_served_chat(message.chat.id)
                await message.stop_propagation()
        except Exception as ex:
            print(ex)
