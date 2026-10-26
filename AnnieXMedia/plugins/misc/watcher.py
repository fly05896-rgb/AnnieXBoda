# Authored By Certified Coders 2026
# File Path: AnnieXMedia/plugins/misc/watcher.py

from contextlib import suppress
from pyrogram import filters
from pyrogram.types import Message

from AnnieXMedia import app
from AnnieXMedia.core.call import StreamController, _clear_
from AnnieXMedia.utils.database import is_active_chat

welcome = 20
close = 30


@app.on_message(filters.video_chat_started, group=welcome)
async def on_vc_started(_, message: Message):
    chat_id = message.chat.id
    with suppress(Exception):
        from AnnieXMedia.plugins.play.rtmp_stereo import is_rtmp_active, _rtmp_call_ready
        if is_rtmp_active(chat_id) or _rtmp_call_ready.get(chat_id):
            return
    if StreamController.is_joining(chat_id) or chat_id in StreamController.active_calls:
        return
    if not await is_active_chat(chat_id):
        await _clear_(chat_id)


@app.on_message(filters.video_chat_ended, group=close)
async def on_vc_ended(_, message: Message):
    chat_id = message.chat.id
    with suppress(Exception):
        from AnnieXMedia.plugins.play.rtmp_stereo import is_rtmp_active
        if is_rtmp_active(chat_id):
            return
    if StreamController.is_joining(chat_id):
        return
    await StreamController.stop_stream(chat_id)
