# Authored By Certified Coders 2026
# File Path: AnnieXMedia/plugins/admins/call_v4_tools.py
# System: Py-TgCalls v4.0.0b4 Controls (Live Audio Quality/Bitrate, Group Call Chat Messages, Audio/Video Recording)

import os
from contextlib import suppress
from pyrogram import filters
from pyrogram.types import Message

import config
from config import BANNED_USERS
from AnnieXMedia import app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.utils.admin_filters import admin_filter

VALID_PRESETS = {
    "master": "MASTER",
    "ماستر": "MASTER",
    "studio": "STUDIO",
    "ستوديو": "STUDIO",
    "high": "HIGH",
    "عالية": "HIGH",
    "عالي": "HIGH",
    "medium": "MEDIUM",
    "متوسط": "MEDIUM",
    "low": "LOW",
    "منخفض": "LOW",
    "voice": "VOICE",
    "عادي": "VOICE",
}


@app.on_message(
    filters.command(["quality", "جودة", "جودة الصوت"], prefixes=["", "/", "!", "."])
    & filters.group
    & admin_filter
    & ~BANNED_USERS
)
async def set_call_quality(_, message: Message):
    chat_id = message.chat.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        current_preset = StreamController.chat_audio_override.get(chat_id)
        cur_txt = (
            f"{current_preset[1] // 1000} kbps"
            if current_preset and current_preset[1]
            else (current_preset[0] if current_preset and current_preset[0] else config.AUDIO_QUALITY)
        )
        return await message.reply_text(
            f"**🎧 جودة الصوت الحالية:** `{cur_txt}`\n\n"
            "**الاستخدام:**\n"
            "• `جودة ماستر` أو `/quality master` — أقصى جودة **510 kbps** ستيريو\n"
            "• `جودة ستوديو` أو `/quality studio` — جودة ستوديو ستيريو\n"
            "• `جودة عالية` أو `/quality high` — جودة عالية ستيريو\n"
            "• `جودة عادي` أو `/quality voice` — صوت المكالمات الرسمي\n"
            "• `/quality 320` — تحديد Bitrate مخصص (من 16 إلى 510 kbps)"
        )

    arg = parts[1].strip().lower()
    if arg.isdigit():
        kbps = int(arg)
        if not (16 <= kbps <= 510):
            return await message.reply_text("**الـ Bitrate لازم يكون بين 16 و 510 kbps.**")
        label = await StreamController.set_audio_quality(chat_id, bitrate_kbps=kbps)
        return await message.reply_text(f"**✅ تم ضبط جودة الصوت على:** `{label}`")

    preset = VALID_PRESETS.get(arg)
    if not preset:
        return await message.reply_text("**خيار غير صحيح. اختر:** `master` / `studio` / `high` / `voice` أو رقم من `16` إلى `510`.")

    label = await StreamController.set_audio_quality(chat_id, preset=preset)
    await message.reply_text(f"**✅ تم ضبط جودة الصوت على:** `{label}`")


@app.on_message(
    filters.command(["vcmsg", "رسالة الكول"], prefixes=["", "/", "!", "."])
    & filters.group
    & admin_filter
    & ~BANNED_USERS
)
async def send_group_call_chat_message(_, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text("**اكتب النص بعد الأمر، مثال:**\n`رسالة الكول أهلاً بكم في الكول`")

    try:
        await StreamController.send_call_message(message.chat.id, parts[1].strip())
        await message.reply_text("**✅ تم إرسال الرسالة داخل شات المكالمة الجماعية.**")
    except Exception as e:
        await message.reply_text(f"**فشل إرسال رسالة المكالمة:**\n`{e}`")


@app.on_message(
    filters.command(["vcrecord", "تسجيل الكول"], prefixes=["", "/", "!", "."])
    & filters.group
    & admin_filter
    & ~BANNED_USERS
)
async def start_vc_recording(_, message: Message):
    chat_id = message.chat.id
    if chat_id in StreamController.active_recordings:
        return await message.reply_text("**التسجيل شغال بالفعل في هذه المكالمة. استخدم `ايقاف التسجيل` لإيقافه.**")

    arg = message.text.split(maxsplit=1)[1].strip().lower() if len(message.text.split(maxsplit=1)) > 1 else "audio"
    is_video = arg in ("video", "فيديو", "mkv", "شاشة")

    try:
        await StreamController.start_recording(chat_id, video=is_video)
        mode_str = "فيديو + مشاركة شاشة (.mkv)" if is_video else "صوت Opus خفيف بدون FFmpeg (.opus)"
        await message.reply_text(f"**🔴 بدأ تسجيل المكالمة بنجاح:** `{mode_str}`\nلإيقاف التسجيل واستلام الملف اكتب: `ايقاف التسجيل`")
    except Exception as e:
        await message.reply_text(f"**فشل بدء التسجيل:**\n`{e}`")


@app.on_message(
    filters.command(["stoprecord", "ايقاف التسجيل"], prefixes=["", "/", "!", "."])
    & filters.group
    & admin_filter
    & ~BANNED_USERS
)
async def stop_vc_recording(_, message: Message):
    chat_id = message.chat.id
    if chat_id not in StreamController.active_recordings:
        return await message.reply_text("**لا يوجد تسجيل نشط حالياً.**")

    status = await message.reply_text("**جاري إيقاف التسجيل وحفظ الملف...**")
    try:
        rec_path = await StreamController.stop_recording(chat_id)
        if rec_path and os.path.exists(rec_path) and os.path.getsize(rec_path) > 0:
            if rec_path.endswith(".mkv"):
                await message.reply_document(rec_path, caption="**🎬 تسجيل المكالمة (فيديو/شاشة — Py-TgCalls v4.0)**")
            else:
                await message.reply_audio(rec_path, caption="**🎙️ تسجيل المكالمة (Opus خفيف — Py-TgCalls v4.0)**")
            with suppress(Exception):
                os.remove(rec_path)
            await status.delete()
        else:
            await status.edit_text("**تم إيقاف التسجيل، لكن الملف فارغ (تأكد أن هناك صوت أو فيديو في المكالمة).**")
    except Exception as e:
        await status.edit_text(f"**حدث خطأ أثناء إنهاء التسجيل:**\n`{e}`")
