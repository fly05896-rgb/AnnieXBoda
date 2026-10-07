# Authored By Certified Coders © 2025
import sys
from pyrogram import Client, errors
from pyrogram.enums import ChatMemberStatus, ParseMode

import config
from ..logging import LOGGER

class MusicBotClient(Client):
    def __init__(self):
        super().__init__(
            name="AnnieXMusic",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,  # تم الإرجاع للسحب من الكونفيج
            workers=48,
            max_concurrent_transmissions=7,
        )
        LOGGER(__name__).info("Bot client initialized.")

    async def start(self):
        await super().start()
        me = await self.get_me()
        
        self.username = me.username
        self.id = me.id
        self.name = f"{me.first_name} {me.last_name or ''}".strip()
        self.mention = me.mention

        try:
            await self.send_message(
                chat_id=config.LOGGER_ID,  # تم الإرجاع للسحب من الكونفيج
                text=(
                    f"<u><b>» {self.mention} ʙᴏᴛ sᴛᴀʀᴛᴇᴅ :</b></u>\n\n"
                    f"ɪᴅ : <code>{self.id}</code>\n"
                    f"ɴᴀᴍᴇ : {self.name}\n"
                    f"ᴜsᴇʀɴᴀᴍᴇ : @{self.username}"
                ),
                parse_mode=ParseMode.HTML
            )
        except (errors.ChannelInvalid, errors.PeerIdInvalid) as e:
            LOGGER(__name__).error(
                f"❌ Bot cannot access the log group/channel.\n"
                f"⚠️ The Exact Error is: {e}\n\n"
                f"💡 Tips to fix (نصائح للحل):\n"
                f"1. تأكد أن البوت مضاف فعلياً في الجروب صاحب الآيدي المكتوب في الكونفيج.\n"
                f"2. أرسل أي رسالة في الجروب واعمل منشن للبوت فيها ليتعرف عليه، ثم أعد تشغيل السيرفر."
            )
            sys.exit()
        except Exception as exc:
            LOGGER(__name__).error(f"❌ Bot has failed to access the log group.\nReason: {type(exc).__name__} - {exc}")
            sys.exit()

        try:
            member = await self.get_chat_member(chat_id=config.LOGGER_ID, user_id=self.id)
            if member.status != ChatMemberStatus.ADMINISTRATOR:
                LOGGER(__name__).error("❌ Promote the bot as admin in the log group/channel (قم برفع البوت كمشرف في الجروب).")
                sys.exit()
        except Exception as e:
            LOGGER(__name__).error(f"❌ Could not check admin status: {e}")
            sys.exit()

        LOGGER(__name__).info(f"✅ Music Bot started as {self.name} (@{self.username})")

