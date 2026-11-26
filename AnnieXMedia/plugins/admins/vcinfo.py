# Authored By Certified Coders 2026
# Module: VC Info - Arabic Commands + Loading Status + Py-TgCalls v4.0 E2E Key Reporting

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from AnnieXMedia import app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.utils.database import group_assistant
from AnnieXMedia.utils.admin_filters import admin_filter


@app.on_message(
    filters.command(["م ف ك", "مين في الكول", "vcinfo", "vcmembers"], prefixes=["", "/", "!", "."])
    & filters.group
    & admin_filter
    & ~BANNED_USERS
)
async def vc_info(client, message: Message):
    chat_id = message.chat.id

    # --- [ رسالة التحقق الأولية ] ---
    status = await message.reply_text("**جـاري التحقق ...**")

    try:
        assistant = await group_assistant(StreamController, chat_id)
        participants = await assistant.get_participants(chat_id)

        if not participants:
            return await status.edit_text("**لا يوجد أعضاء في المحادثة الصوتية حاليا.**")

        msg_lines = ["**معلومات أعضاء المكالمة:**\n"]
        for p in participants:
            try:
                user = await app.get_users(p.user_id)
                name = user.mention if user else f"<code>{p.user_id}</code>"
            except Exception:
                name = f"<code>{p.user_id}</code>"

            mute_status = "[مكتوم]" if p.muted else "[متحدث]"

            screen_status = ""
            if getattr(p, "screen_sharing", False):
                screen_status = " | [مشاركة شاشة]"
            elif getattr(p, "video", False):
                screen_status = " | [كاميرا]"

            e2e_status = " | [E2E]" if getattr(p, "has_e2e_key", False) else ""
            volume_level = getattr(p, "volume", "غير متاح")

            info = f"{mute_status} {name} | الصوت: {volume_level}{screen_status}{e2e_status}"
            msg_lines.append(info)

        msg_lines.append(f"\nالعدد الكلي: **{len(participants)}**")

        await status.edit_text("\n".join(msg_lines))

    except Exception as e:
        await status.edit_text(f"**فشل جلب المعلومات:**\n`{e}`")
