# Authored By Certified Coders © 2026
import asyncio
import functools
import inspect
import sys
from contextlib import suppress

from pyrogram import Client, errors
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import LinkPreviewOptions, Message

import config
from ..logging import LOGGER


def _apply_kurigram_compat() -> None:
    """
    طبقة توافق ذكية مع Kurigram 2.2.26:
    تحول disable_web_page_preview تلقائياً إلى LinkPreviewOptions
    وتعالج معاملات quote و delete_after المحذوفة من Message.reply_* و Message.edit_*
    """
    if getattr(Message, "_kurigram_patched", False):
        return

    target_methods = [
        "reply",
        "reply_text",
        "edit",
        "edit_text",
        "reply_photo",
        "reply_video",
        "reply_audio",
        "reply_document",
        "reply_animation",
        "reply_sticker",
        "reply_voice",
    ]

    for method_name in target_methods:
        orig_method = getattr(Message, method_name, None)
        if not orig_method or not callable(orig_method):
            continue

        try:
            sig = inspect.signature(orig_method)
            accepted_params = set(sig.parameters.keys())
            has_varkw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except Exception:
            continue

        if has_varkw:
            continue

        def _make_wrapper(fn, valid_params):
            @functools.wraps(fn)
            async def _wrapper(self, *args, **kwargs):
                dwp = kwargs.pop("disable_web_page_preview", None)
                if dwp is not None and "link_preview_options" in valid_params:
                    if kwargs.get("link_preview_options") is None:
                        kwargs["link_preview_options"] = LinkPreviewOptions(
                            is_disabled=bool(dwp)
                        )

                if "quote" not in valid_params:
                    kwargs.pop("quote", None)

                delete_after = kwargs.pop("delete_after", None) if "delete_after" not in valid_params else None

                res = await fn(self, *args, **kwargs)

                if delete_after and isinstance(res, Message):
                    async def _del_later(msg: Message, delay: float):
                        await asyncio.sleep(delay)
                        with suppress(Exception):
                            await msg.delete()

                    asyncio.create_task(_del_later(res, float(delete_after)))

                return res

            return _wrapper

        setattr(Message, method_name, _make_wrapper(orig_method, accepted_params))

    Message._kurigram_patched = True


_apply_kurigram_compat()


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
        LOGGER(__name__).info("Bot client initialized with Kurigram 2.2.26 compatibility.")

    async def start(self):
        await super().start()
        me = await self.get_me()

        self.username = me.username
        self.id = me.id
        self.name = f"{me.first_name} {me.last_name or ''}".strip()
        self.mention = me.mention

        try:
            await self.send_message(
                chat_id=config.LOGGER_ID,
                text=(
                    f"<u><b>» {self.mention} ʙᴏᴛ sᴛᴀʀᴛᴇᴅ :</b></u>\n\n"
                    f"ɪᴅ : <code>{self.id}</code>\n"
                    f"ɴᴀᴍᴇ : {self.name}\n"
                    f"ᴜsᴇʀɴᴀᴍᴇ : @{self.username}"
                ),
                parse_mode=ParseMode.HTML,
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
            LOGGER(__name__).error(
                f"❌ Bot has failed to access the log group.\nReason: {type(exc).__name__} - {exc}"
            )
            sys.exit()

        try:
            member = await self.get_chat_member(chat_id=config.LOGGER_ID, user_id=self.id)
            if member.status != ChatMemberStatus.ADMINISTRATOR:
                LOGGER(__name__).error(
                    "❌ Promote the bot as admin in the log group/channel (قم برفع البوت كمشرف في الجروب)."
                )
                sys.exit()
        except Exception as e:
            LOGGER(__name__).error(f"❌ Could not check admin status: {e}")
            sys.exit()

        LOGGER(__name__).info(f"✅ Music Bot started as {self.name} (@{self.username})")
