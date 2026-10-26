# Authored By Certified Coders 2026
# File Path: AnnieXMedia/utils/decorators/admins.py

from contextlib import suppress
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from AnnieXMedia import app
from AnnieXMedia.misc import SUDOERS, db
from AnnieXMedia.utils.database import (
    get_authuser_names,
    get_cmode,
    get_lang,
    get_upvote_count,
    is_active_chat,
    is_maintenance,
    is_nonadmin_chat,
    is_skipmode,
)
from config import SUPPORT_CHAT, adminlist, confirmer
from strings import get_string

from ..formatters import int_to_alpha


async def _is_authorized_admin(chat_id: int, user_id: int) -> bool:
    """فحص شامل للمشرفين والادمنية المرفوعين بالبوت مع تحديث تلقائي للكاش بدون طلب /reload"""
    if user_id in SUDOERS:
        return True
    admins = adminlist.get(chat_id)
    if admins and user_id in admins:
        return True
    with suppress(Exception):
        token = await int_to_alpha(user_id)
        auth_names = await get_authuser_names(chat_id)
        if token in auth_names:
            adminlist.setdefault(chat_id, []).append(user_id)
            return True
    with suppress(Exception):
        member = await app.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER:
            adminlist.setdefault(chat_id, []).append(user_id)
            return True
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if not member.privileges or member.privileges.can_manage_video_chats:
                adminlist.setdefault(chat_id, []).append(user_id)
                return True
    return False


def AdminRightsCheck(mystic):
    async def wrapper(client, message):
        if isinstance(message, CallbackQuery):
            cb = message
            if await is_maintenance() is False and cb.from_user.id not in SUDOERS:
                return await cb.answer(
                    "البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة مجموعة الدعم لمعرفة السبب.",
                    show_alert=True,
                )

            try:
                language = await get_lang(cb.message.chat.id)
                _ = get_string(language)
            except Exception:
                _ = get_string("ar")

            chat_id = cb.message.chat.id
            if not await is_nonadmin_chat(chat_id):
                if not await _is_authorized_admin(chat_id, cb.from_user.id):
                    return await cb.answer(
                        "عذرا، يجب ان تكون مشرفا وتمتلك صلاحية ادارة المحادثات الصوتية.",
                        show_alert=True,
                    )

            return await mystic(client, cb, _, chat_id)

        if await is_maintenance() is False and message.from_user.id not in SUDOERS:
            return await message.reply_text(
                text=f"البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة <a href={SUPPORT_CHAT}>مجموعة الدعم</a> لمعرفة السبب وتفاصيل التحديث.",
                disable_web_page_preview=True,
            )

        with suppress(Exception):
            await message.delete()

        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("ar")

        if message.sender_chat:
            upl = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="كيفية اصلاح المشكلة ؟",
                            callback_data="AnonymousAdmin",
                        ),
                    ]
                ]
            )
            return await message.reply_text(
                "عذرا، انت تكتب بصفتك قناة او مشرف متخفي. يرجى اظهار حسابك الشخصي لتتمكن من استخدام اوامر البوت.",
                reply_markup=upl,
            )

        cmd = message.command or []
        if cmd and cmd[0] and cmd[0][0] == "c":
            chat_id = await get_cmode(message.chat.id)
            if chat_id is None:
                return await message.reply_text(
                    "لم يتم تحديد مجموعة للتشغيل عن بعد. يرجى تحديد المجموعة اولا."
                )
            try:
                await app.get_chat(chat_id)
            except Exception:
                return await message.reply_text(
                    "غير قادر على الوصول الى المجموعة المحددة. تاكد من ان البوت مشرف هناك."
                )
        else:
            chat_id = message.chat.id

        if not await is_active_chat(chat_id):
            return await message.reply_text(
                "لا توجد محادثة صوتية نشطة حاليا. يرجى تشغيل شيء اولا لتتمكن من استخدام هذا الامر."
            )

        if not await is_nonadmin_chat(message.chat.id):
            if not await _is_authorized_admin(message.chat.id, message.from_user.id):
                if await is_skipmode(message.chat.id):
                    upvote = await get_upvote_count(chat_id)
                    text = (
                        f"<b>عذرا، انت لا تمتلك صلاحيات المشرف</b>\n\n"
                        f"لتحديث قائمة المشرفين في ذاكرة البوت يرجى ارسال امر : /reload\n\n"
                        f"نظام التصويت مفعل: مطلوب عدد {upvote} اصوات من الاعضاء لتخطي هذا المسار."
                    )

                    command = cmd[0] if cmd else ""
                    if command and command[0] == "c":
                        command = command[1:]
                    if command == "speed":
                        return await message.reply_text(
                            "عذرا، يجب ان تكون من مشرفي المجموعة او من الادارة للتحكم في سرعة التشغيل."
                        )

                    MODE = command.title() if command else ""
                    upl = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    text="تصويت لتخطي المسار",
                                    callback_data=f"ADMIN  UpVote|{chat_id}_{MODE}",
                                ),
                            ]
                        ]
                    )
                    if chat_id not in confirmer:
                        confirmer[chat_id] = {}
                    try:
                        vidid = db[chat_id][0]["vidid"]
                        file = db[chat_id][0]["file"]
                    except Exception:
                        return await message.reply_text(
                            "عذرا، يجب ان تكون مشرفا في المجموعة لتنفيذ هذا الامر."
                        )

                    senn = await message.reply_text(text, reply_markup=upl)
                    confirmer[chat_id][senn.id] = {
                        "vidid": vidid,
                        "file": file,
                    }
                    return
                else:
                    return await message.reply_text(
                        "عذرا، يجب ان تكون مشرفا في المجموعة للتحكم في اوامر التشغيل المتقدمة."
                    )

        return await mystic(client, message, _, chat_id)

    return wrapper


def AdminActual(mystic):
    async def wrapper(client, message):
        if await is_maintenance() is False and message.from_user.id not in SUDOERS:
            return await message.reply_text(
                text=f"البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة <a href={SUPPORT_CHAT}>مجموعة الدعم</a> لمعرفة السبب وتفاصيل التحديث.",
                disable_web_page_preview=True,
            )

        with suppress(Exception):
            await message.delete()

        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("ar")

        if message.sender_chat:
            upl = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="كيفية اصلاح المشكلة ؟",
                            callback_data="AnonymousAdmin",
                        ),
                    ]
                ]
            )
            return await message.reply_text(
                "عذرا، انت تكتب بصفتك قناة او مشرف متخفي. يرجى اظهار حسابك الشخصي لتتمكن من استخدام اوامر البوت.",
                reply_markup=upl,
            )

        if message.from_user.id not in SUDOERS:
            try:
                member = await app.get_chat_member(message.chat.id, message.from_user.id)
                priv = member.privileges
                if member.status != ChatMemberStatus.OWNER and not priv:
                    return await message.reply_text(
                        "عذرا، يجب ان تكون مشرفا في المجموعة وتمتلك صلاحيات الادارة لتنفيذ هذا الامر."
                    )
                if member.status != ChatMemberStatus.OWNER and not priv.can_manage_video_chats:
                    return await message.reply(
                        "عذرا، انت لا تمتلك صلاحية ادارة المحادثات الصوتية."
                    )
            except Exception:
                return

        return await mystic(client, message, _)

    return wrapper


def ActualAdminCB(mystic):
    async def wrapper(client, CallbackQuery):
        if await is_maintenance() is False and CallbackQuery.from_user.id not in SUDOERS:
            return await CallbackQuery.answer(
                "البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة مجموعة الدعم لمعرفة السبب.",
                show_alert=True,
            )
        try:
            language = await get_lang(CallbackQuery.message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("ar")

        if CallbackQuery.message.chat.type == ChatType.PRIVATE:
            return await mystic(client, CallbackQuery, _)

        chat_id = CallbackQuery.message.chat.id
        if not await is_nonadmin_chat(chat_id):
            if not await _is_authorized_admin(chat_id, CallbackQuery.from_user.id):
                return await CallbackQuery.answer(
                    "عذرا، انت لا تمتلك صلاحية ادارة المحادثات الصوتية. لا يمكنك التفاعل مع هذا الزر.",
                    show_alert=True,
                )

        return await mystic(client, CallbackQuery, _)

    return wrapper
