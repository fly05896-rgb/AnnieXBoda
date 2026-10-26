# Authored By Certified Coders © 2025
from pyrogram.enums import ChatType
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


def AdminRightsCheck(mystic):
    async def wrapper(client, message):
        # ══════════════════════════════════════════════════════════
        # مسار خاص بـ CallbackQuery (مثل أزرار rtmp_ctl)
        # ══════════════════════════════════════════════════════════
        if isinstance(message, CallbackQuery):
            cb = message

            if await is_maintenance() is False:
                if cb.from_user.id not in SUDOERS:
                    return await cb.answer(
                        "البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة مجموعة الدعم لمعرفة السبب.",
                        show_alert=True,
                    )

            try:
                language = await get_lang(cb.message.chat.id)
                _ = get_string(language)
            except:
                _ = get_string("en")

            chat_id = cb.message.chat.id

            is_non_admin = await is_nonadmin_chat(chat_id)
            if not is_non_admin:
                if cb.from_user.id not in SUDOERS:
                    try:
                        a = (
                            await app.get_chat_member(chat_id, cb.from_user.id)
                        ).privileges
                        if not a or not a.can_manage_video_chats:
                            return await cb.answer(
                                "عذرا، يجب أن تكون مشرفا وتمتلك صلاحية إدارة المحادثات الصوتية.",
                                show_alert=True,
                            )
                    except:
                        return await cb.answer(
                            "عذرا، يجب أن تكون مشرفا وتمتلك صلاحية إدارة المحادثات الصوتية.",
                            show_alert=True,
                        )

            return await mystic(client, cb, _, chat_id)

        # ══════════════════════════════════════════════════════════
        # المسار العادي للرسائل
        # ══════════════════════════════════════════════════════════
        if await is_maintenance() is False:
            if message.from_user.id not in SUDOERS:
                return await message.reply_text(
                    text=f"البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة <a href={SUPPORT_CHAT}>مجموعة الدعم</a> لمعرفة السبب وتفاصيل التحديث.",
                    disable_web_page_preview=True,
                )

        try:
            await message.delete()
        except:
            pass

        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except:
            _ = get_string("en")

        if message.sender_chat:
            upl = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="كيفية إصلاح المشكلة ؟",
                            callback_data="AnonymousAdmin",
                        ),
                    ]
                ]
            )
            return await message.reply_text(
                "عذرا، أنت تكتب بصفتك قناة أو مشرف متخفي. يرجى إظهار حسابك الشخصي لتتمكن من استخدام أوامر البوت.",
                reply_markup=upl,
            )

        # ✅ الإصلاح الأساسي: حماية من message.command = None
        cmd = message.command or []

        if cmd and cmd[0] and cmd[0][0] == "c":
            chat_id = await get_cmode(message.chat.id)
            if chat_id is None:
                return await message.reply_text(
                    "لم يتم تحديد مجموعة للتشغيل عن بعد. يرجى تحديد المجموعة أولا."
                )
            try:
                await app.get_chat(chat_id)
            except:
                return await message.reply_text(
                    "غير قادر على الوصول إلى المجموعة المحددة. تأكد من أن البوت مشرف هناك."
                )
        else:
            chat_id = message.chat.id

        if not await is_active_chat(chat_id):
            return await message.reply_text(
                "لا توجد محادثة صوتية نشطة حاليا. يرجى تشغيل شيء أولا لتتمكن من استخدام هذا الأمر."
            )

        is_non_admin = await is_nonadmin_chat(message.chat.id)
        if not is_non_admin:
            if message.from_user.id not in SUDOERS:
                admins = adminlist.get(message.chat.id)
                if not admins:
                    return await message.reply_text(
                        "لا يوجد مشرفين مسجلين في ذاكرة البوت. يرجى كتابة أمر (تحديث) أو (reload) لتحديث قائمة المشرفين."
                    )
                else:
                    if message.from_user.id not in admins:
                        if await is_skipmode(message.chat.id):
                            upvote = await get_upvote_count(chat_id)
                            text = f"""<b>عذرا، أنت لا تمتلك صلاحيات المشرف</b>

لتحديث قائمة المشرفين في ذاكرة البوت يرجى إرسال أمر : /reload

نظام التصويت مفعل: مطلوب عدد {upvote} أصوات من الأعضاء لتخطي هذا المسار."""

                            # ✅ حماية إضافية عند الوصول للعنصر الأول
                            command = cmd[0] if cmd else ""
                            if command and command[0] == "c":
                                command = command[1:]
                            if command == "speed":
                                return await message.reply_text(
                                    "عذرا، يجب أن تكون من مشرفي المجموعة أو من الإدارة للتحكم في سرعة التشغيل."
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
                            except:
                                return await message.reply_text(
                                    "عذرا، يجب أن تكون مشرفا في المجموعة لتنفيذ هذا الأمر."
                                )

                            senn = await message.reply_text(text, reply_markup=upl)
                            confirmer[chat_id][senn.id] = {
                                "vidid": vidid,
                                "file": file,
                            }
                            return
                        else:
                            return await message.reply_text(
                                "عذرا، يجب أن تكون مشرفا في المجموعة للتحكم في أوامر التشغيل المتقدمة."
                            )

        return await mystic(client, message, _, chat_id)

    return wrapper


def AdminActual(mystic):
    async def wrapper(client, message):
        if await is_maintenance() is False:
            if message.from_user.id not in SUDOERS:
                return await message.reply_text(
                    text=f"البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة <a href={SUPPORT_CHAT}>مجموعة الدعم</a> لمعرفة السبب وتفاصيل التحديث.",
                    disable_web_page_preview=True,
                )

        try:
            await message.delete()
        except:
            pass

        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except:
            _ = get_string("en")

        if message.sender_chat:
            upl = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="كيفية إصلاح المشكلة ؟",
                            callback_data="AnonymousAdmin",
                        ),
                    ]
                ]
            )
            return await message.reply_text(
                "عذرا، أنت تكتب بصفتك قناة أو مشرف متخفي. يرجى إظهار حسابك الشخصي لتتمكن من استخدام أوامر البوت.",
                reply_markup=upl,
            )

        if message.from_user.id not in SUDOERS:
            try:
                member = (
                    await app.get_chat_member(message.chat.id, message.from_user.id)
                ).privileges
                if not member:
                    return await message.reply_text(
                        "عذرا، يجب أن تكون مشرفا في المجموعة وتمتلك صلاحيات الإدارة لتنفيذ هذا الأمر."
                    )
            except:
                return
            if not member.can_manage_video_chats:
                return await message.reply(
                    "عذرا، أنت لا تمتلك صلاحية (إدارة المحادثات الصوتية). يرجى الطلب من مالك المجموعة منحك هذه الصلاحية لتتمكن من التحكم بالبوت."
                )

        return await mystic(client, message, _)

    return wrapper


def ActualAdminCB(mystic):
    async def wrapper(client, CallbackQuery):
        if await is_maintenance() is False:
            if CallbackQuery.from_user.id not in SUDOERS:
                return await CallbackQuery.answer(
                    "البوت حاليا في وضع الصيانة والتحديث. يرجى زيارة مجموعة الدعم لمعرفة السبب.",
                    show_alert=True,
                )
        try:
            language = await get_lang(CallbackQuery.message.chat.id)
            _ = get_string(language)
        except:
            _ = get_string("en")

        if CallbackQuery.message.chat.type == ChatType.PRIVATE:
            return await mystic(client, CallbackQuery, _)

        is_non_admin = await is_nonadmin_chat(CallbackQuery.message.chat.id)
        if not is_non_admin:
            try:
                a = (
                    await app.get_chat_member(
                        CallbackQuery.message.chat.id,
                        CallbackQuery.from_user.id,
                    )
                ).privileges
                if not a:
                    return await CallbackQuery.answer(
                        "عذرا، يجب أن تكون مشرفا وتمتلك صلاحيات الإدارة لاستخدام هذه الأزرار.",
                        show_alert=True,
                    )
            except:
                return await CallbackQuery.answer(
                    "عذرا، يجب أن تكون مشرفا وتمتلك صلاحيات الإدارة لاستخدام هذه الأزرار.",
                    show_alert=True,
                )

            if not a.can_manage_video_chats:
                if CallbackQuery.from_user.id not in SUDOERS:
                    token = await int_to_alpha(CallbackQuery.from_user.id)
                    _check = await get_authuser_names(CallbackQuery.from_user.id)
                    if token not in _check:
                        try:
                            return await CallbackQuery.answer(
                                "عذرا، أنت لا تمتلك صلاحية إدارة المحادثات الصوتية. لا يمكنك التفاعل مع هذا الزر.",
                                show_alert=True,
                            )
                        except:
                            return

        return await mystic(client, CallbackQuery, _)

    return wrapper
