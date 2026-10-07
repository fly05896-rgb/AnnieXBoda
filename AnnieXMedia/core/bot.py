# Authored By Certified Coders © 2025
import sys
from pyrogram import Client, errors
from pyrogram.enums import ChatMemberStatus

import config
from ..logging import LOGGER


class MusicBotClient(Client):
    def __init__(self):
        super().__init__(
            name="AnnieXMusic",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            workers=48,
            max_concurrent_transmissions=7,
        )
        LOGGER(__name__).info("Bot client initialized.")

    async def start(self):
        await super().start()
        me = await self.get_me()
        self.username, self.id = me.username, me.id
        self.name = f"{me.first_name} {me.last_name or ''}".strip()
        self.mention = me.mention

        try:
            await self.send_message(
                config.LOGGER_ID,
                (
                    f"<u><b>» {self.mention} ʙᴏᴛ sᴛᴀʀᴛᴇᴅ :</b></u>\n\n"
                    f"ɪᴅ : <code>{self.id}</code>\n"
                    f"ɴᴀᴍᴇ : {self.name}\n"
                    f"ᴜsᴇʀɴᴀᴍᴇ : @{self.username}"
                ),
            )
        except (errors.ChannelInvalid, errors.PeerIdInvalid) as e:
            # هنا تم التعديل لعرض رسالة الخطأ الأصلية من تيليجرام
            LOGGER(__name__).error(
                f"❌ Bot cannot access the log group/channel.\n"
                f"⚠️ The Exact Error is: {e}\n\n"
                f"💡 Tips to fix (نصائح للحل):\n"
                f"1. تأكد أن معرف الجروب LOGGER_ID في ملف الـ config يبدأ بـ '-100'.\n"
                f"2. أو استخدم يوزر نيم الجروب مباشرة (مثال: '@MyLogGroup') بدلاً من الأرقام.\n"
                f"3. اكتب أي رسالة في جروب السجل واعمل منشن للبوت فيها، ثم أعد تشغيل السيرفر."
            )
            sys.exit()
        except Exception as exc:
            # تم إضافة {exc} لعرض الخطأ الفعلي بدلاً من اسمه فقط
            LOGGER(__name__).error(f"❌ Bot has failed to access the log group.\nReason: {type(exc).__name__} - {exc}")
            sys.exit()

        try:
            member = await self.get_chat_member(config.LOGGER_ID, self.id)
            if member.status != ChatMemberStatus.ADMINISTRATOR:
                LOGGER(__name__).error("❌ Promote the bot as admin in the log group/channel.")
                sys.exit()
        except Exception as e:
            LOGGER(__name__).error(f"❌ Could not check admin status: {e}")
            sys.exit()

        LOGGER(__name__).info(f"✅ Music Bot started as {self.name} (@{self.username})")
