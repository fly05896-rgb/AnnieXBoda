import asyncio
import functools
import inspect
import sys
from contextlib import suppress
from typing import Any, Optional, Tuple

import pyrogram.types as _pg_types
from pyrogram import Client, ContinuePropagation, StopPropagation, errors, filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import CallbackQuery, Message

import config
from ..logging import LOGGER

if hasattr(_pg_types, "LinkPreviewOptions"):
    LinkPreviewOptions = _pg_types.LinkPreviewOptions
else:
    class LinkPreviewOptions:
        __slots__ = (
            "is_disabled",
            "url",
            "prefer_small_media",
            "prefer_large_media",
            "show_above_text",
        )

        def __init__(
            self,
            is_disabled: bool = False,
            url: Optional[str] = None,
            prefer_small_media: bool = False,
            prefer_large_media: bool = False,
            show_above_text: bool = False,
        ):
            self.is_disabled = bool(is_disabled)
            self.url = url
            self.prefer_small_media = prefer_small_media
            self.prefer_large_media = prefer_large_media
            self.show_above_text = show_above_text

    setattr(_pg_types, "LinkPreviewOptions", LinkPreviewOptions)


def _patch_method(cls: Any, method_name: str) -> None:
    orig_method = getattr(cls, method_name, None)
    if not orig_method or not callable(orig_method):
        return

    try:
        sig = inspect.signature(orig_method)
        accepted_params = set(sig.parameters.keys())
        has_varkw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except Exception:
        return

    if has_varkw:
        return

    def _make_wrapper(fn, valid_params):
        @functools.wraps(fn)
        async def _wrapper(self, *args, **kwargs):
            dwp = kwargs.get("disable_web_page_preview")
            lpo = kwargs.get("link_preview_options")

            if (
                "link_preview_options" in valid_params
                and "disable_web_page_preview" not in valid_params
            ):
                kwargs.pop("disable_web_page_preview", None)
                if dwp is not None and lpo is None:
                    kwargs["link_preview_options"] = LinkPreviewOptions(
                        is_disabled=bool(dwp)
                    )
            elif (
                "disable_web_page_preview" in valid_params
                and "link_preview_options" not in valid_params
            ):
                kwargs.pop("link_preview_options", None)
                if lpo is not None and dwp is None:
                    is_dis = getattr(lpo, "is_disabled", None)
                    if is_dis is not None:
                        kwargs["disable_web_page_preview"] = bool(is_dis)
            else:
                if "disable_web_page_preview" not in valid_params:
                    kwargs.pop("disable_web_page_preview", None)
                if "link_preview_options" not in valid_params:
                    kwargs.pop("link_preview_options", None)

            if "quote" not in valid_params:
                kwargs.pop("quote", None)

            delete_after = (
                kwargs.pop("delete_after", None)
                if "delete_after" not in valid_params
                else None
            )

            try:
                res = await fn(self, *args, **kwargs)
            except errors.FloodWait as fw:
                wait_s = int(getattr(fw, "value", getattr(fw, "x", 1)) or 1)
                if wait_s <= 15:
                    await asyncio.sleep(wait_s + 0.5)
                    res = await fn(self, *args, **kwargs)
                else:
                    raise

            if delete_after and isinstance(res, Message):
                async def _del_later(msg: Message, delay: float):
                    await asyncio.sleep(delay)
                    with suppress(Exception):
                        await msg.delete()

                asyncio.create_task(_del_later(res, float(delete_after)))

            return res

        return _wrapper

    setattr(cls, method_name, _make_wrapper(orig_method, accepted_params))


def _apply_kurigram_compat() -> None:
    if getattr(Message, "_kurigram_patched", False):
        return

    msg_methods = (
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
    )
    for m in msg_methods:
        _patch_method(Message, m)

    for m in ("edit_message_text", "edit_message_caption", "edit_message_media"):
        _patch_method(CallbackQuery, m)

    for m in (
        "send_message",
        "send_photo",
        "send_video",
        "send_audio",
        "send_document",
        "edit_message_text",
        "edit_message_caption",
        "edit_message_media",
    ):
        _patch_method(Client, m)

    Message._kurigram_patched = True


_apply_kurigram_compat()


class _NativeAskEngine:
    def __init__(self):
        self._waiters: dict[Tuple[int, Optional[int]], asyncio.Future] = {}
        self._installed: set[int] = set()

    def ensure_installed(self, client: Client) -> None:
        cid = id(client)
        if cid in self._installed:
            return
        self._installed.add(cid)

        async def _intercept_incoming(_: Client, message: Message):
            if not self._waiters or not message.chat:
                raise ContinuePropagation

            chat_id = message.chat.id
            user_id = message.from_user.id if message.from_user else None

            fut = self._waiters.get((chat_id, user_id)) or self._waiters.get(
                (chat_id, None)
            )
            if fut and not fut.done():
                fut.set_result(message)
                raise StopPropagation

            raise ContinuePropagation

        client.add_handler(
            MessageHandler(_intercept_incoming, filters.all), group=-999
        )

    async def listen(
        self,
        client: Client,
        chat_id: int,
        user_id: Optional[int] = None,
        timeout: Optional[float] = 30.0,
    ) -> Message:
        self.ensure_installed(client)
        key = (int(chat_id), int(user_id) if user_id is not None else None)
        old = self._waiters.pop(key, None)
        if old and not old.done():
            old.cancel()

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._waiters[key] = fut
        try:
            if timeout:
                return await asyncio.wait_for(fut, timeout=float(timeout))
            return await fut
        finally:
            if self._waiters.get(key) is fut:
                self._waiters.pop(key, None)

    async def ask(
        self,
        client: Client,
        chat_id: int,
        text: str,
        user_id: Optional[int] = None,
        timeout: Optional[float] = 30.0,
        *args,
        **kwargs,
    ) -> Message:
        kwargs.pop("filters", None)
        sent = await client.send_message(chat_id, text, *args, **kwargs)
        response = await self.listen(
            client, chat_id=chat_id, user_id=user_id, timeout=timeout
        )
        response.sent_message = sent
        return response


_ask_engine = _NativeAskEngine()


class MusicBotClient(Client):
    def __init__(self):
        init_sig = inspect.signature(Client.__init__).parameters
        extra_kwargs: dict[str, Any] = {}

        if "in_memory" in init_sig:
            extra_kwargs["in_memory"] = True
        if "skip_updates" in init_sig:
            extra_kwargs["skip_updates"] = True
        if "sleep_threshold" in init_sig:
            extra_kwargs["sleep_threshold"] = 30
        if "fetch_stories" in init_sig:
            extra_kwargs["fetch_stories"] = False
        if "fetch_topics" in init_sig:
            extra_kwargs["fetch_topics"] = False
        if "fetch_stickers" in init_sig:
            extra_kwargs["fetch_stickers"] = False
        if "link_preview_options" in init_sig:
            extra_kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)

        super().__init__(
            name="AnnieXMusic",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            workers=48,
            max_concurrent_transmissions=16,
            **extra_kwargs,
        )

        if not hasattr(self, "ask"):
            self.ask = functools.partial(_ask_engine.ask, self)
        if not hasattr(self, "listen"):
            self.listen = functools.partial(_ask_engine.listen, self)

        LOGGER(__name__).info("MusicBotClient initialized.")

    async def start(self):
        await super().start()
        _ask_engine.ensure_installed(self)

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
                f"Bot cannot access the log group/channel. Error: {e}"
            )
            sys.exit()
        except Exception as exc:
            LOGGER(__name__).error(
                f"Bot failed to access the log group. Reason: {type(exc).__name__} - {exc}"
            )
            sys.exit()

        try:
            member = await self.get_chat_member(
                chat_id=config.LOGGER_ID, user_id=self.id
            )
            if member.status != ChatMemberStatus.ADMINISTRATOR:
                LOGGER(__name__).error(
                    "Promote the bot as admin in the log group/channel."
                )
                sys.exit()
        except Exception as e:
            LOGGER(__name__).error(f"Could not check admin status: {e}")
            sys.exit()

        LOGGER(__name__).info(f"Music Bot started as {self.name} (@{self.username})")
