# Authored By Certified Coders 2026
# Module: Help Menu & Commands - Arabic & No Emojis

import re
from typing import Union

from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, Message

from AnnieXMedia import app
from AnnieXMedia.utils.database import get_lang
from AnnieXMedia.utils.decorators.language import LanguageStart, languageCB
from AnnieXMedia.utils.inline.help import (
    action_sub_menu,
    first_page,
    help_back_markup,
    private_help_panel,
    second_page,
)
from AnnieXMedia.utils.inline.start import private_panel
from config import BANNED_USERS, HELP_IMG_URL, SUPPORT_CHAT
from strings import get_string, helpers

# ────────────────────────────────────────────────  /help entrypoints ──

# نقطة الدخول في الخاص (Private)
@app.on_message(
    filters.command(["help", "مساعدة", "الاوامر", "اوامر"], prefixes=["", "/", "!", "."]) 
    & filters.private 
    & ~BANNED_USERS
)
@app.on_callback_query(filters.regex("open_help") & ~BANNED_USERS)
@LanguageStart
async def helper_private(client: Client, update: Union[Message, types.CallbackQuery], _):
    is_cb = isinstance(update, types.CallbackQuery)
    language = await get_lang(update.from_user.id)
    _ = get_string(language)

    keyboard = first_page(_)
    caption = _["help_1"].format(SUPPORT_CHAT)

    if is_cb:
        await update.answer()
        await update.message.edit_caption(caption, reply_markup=keyboard)
    else:
        await update.delete()
        await update.reply_photo(
            photo=HELP_IMG_URL,
            caption=caption,
            reply_markup=keyboard
        )

# ────────────────────────────────────────────────  group /help notice ─

# نقطة الدخول في المجموعات (Groups)
@app.on_message(
    filters.command(["help", "مساعدة", "الاوامر", "اوامر"], prefixes=["", "/", "!", "."]) 
    & filters.group 
    & ~BANNED_USERS
)
@LanguageStart
async def help_com_group(client: Client, message: Message, _):
    keyboard = private_help_panel(_)
    # استخدام الدوال الحديثة لتعطيل معاينة الروابط بدلاً من disable_web_page_preview
    await message.reply_text(
        _["help_2"],
        reply_markup=InlineKeyboardMarkup(keyboard),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

# ────────────────────────────────────────────────  main help buttons ──

@app.on_callback_query(filters.regex(r"help_callback hb(\d+)_p(\d+)") & ~BANNED_USERS)
@languageCB
async def helper_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    match = re.match(r"help_callback hb(\d+)_p(\d+)", CallbackQuery.data)
    if not match:
        return await CallbackQuery.answer("بيانات غير صالحة.", show_alert=True)

    number = int(match.group(1))
    current_page = int(match.group(2))

    #── Action (1) gets its own sub-menu
    if number == 1:
        await CallbackQuery.edit_message_text(
            _["S_B_M"],
            reply_markup=action_sub_menu(_, current_page),
            link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )
        return

    #── All other categories
    help_text = getattr(helpers, f"HELP_{number}", None)
    if not help_text:
        return await CallbackQuery.answer("موضوع المساعدة غير صالح.", show_alert=True)

    await CallbackQuery.edit_message_text(
        help_text,
        reply_markup=help_back_markup(_, current_page),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

# ─────────────────────────────────────────  pagination callbacks ─────

@app.on_callback_query(filters.regex(r"help_next_(\d+)") & ~BANNED_USERS)
@languageCB
async def help_next_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    if CallbackQuery.data == "help_next_2":
        await CallbackQuery.edit_message_text(
            _["help_1"].format(SUPPORT_CHAT),
            reply_markup=second_page(_),
            link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )
    else:
        await CallbackQuery.answer("لا توجد صفحات اخرى.", show_alert=True)

@app.on_callback_query(filters.regex(r"help_prev_(\d+)") & ~BANNED_USERS)
@languageCB
async def help_prev_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    if CallbackQuery.data == "help_prev_1":
        await CallbackQuery.edit_message_text(
            _["help_1"].format(SUPPORT_CHAT),
            reply_markup=first_page(_),
            link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )
    else:
        await CallbackQuery.answer("لا توجد صفحات سابقة.", show_alert=True)

@app.on_callback_query(filters.regex(r"help_back_(\d+)") & ~BANNED_USERS)
@languageCB
async def help_back_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    page = CallbackQuery.data.split("_")[-1]
    if page == "1":
        keyboard = first_page(_)
    elif page == "2":
        keyboard = second_page(_)
    else:
        return await CallbackQuery.answer("صفحة غير صالحة.", show_alert=True)

    await CallbackQuery.edit_message_text(
        _["help_1"].format(SUPPORT_CHAT),
        reply_markup=keyboard,
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

# ────────────────────────────────────────  sub-topic buttons (Action) ─

@app.on_callback_query(filters.regex("action_prom_1") & ~BANNED_USERS)
@languageCB
async def action_prom_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    await CallbackQuery.edit_message_text(
        helpers.HELP_1_PROMO,
        reply_markup=help_back_markup(_, 1),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

@app.on_callback_query(filters.regex("action_pun_1") & ~BANNED_USERS)
@languageCB
async def action_pun_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    await CallbackQuery.edit_message_text(
        helpers.HELP_1_PUNISH,
        reply_markup=help_back_markup(_, 1),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

# ────────────────────────────────────────────────  back to start panel ─

@app.on_callback_query(filters.regex("back_to_main") & ~BANNED_USERS)
@languageCB
async def back_to_main_cb(client: Client, CallbackQuery: types.CallbackQuery, _):
    out = private_panel(_)
    await CallbackQuery.edit_message_caption(
        _["start_2"].format(
            CallbackQuery.from_user.mention, app.mention
        ),
        reply_markup=InlineKeyboardMarkup(out)
    )
