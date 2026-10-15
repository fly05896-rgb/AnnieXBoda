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
    # إذا كان المساعد هو من يقوم بفتح الكول حالياً أو هناك بث نشط بالفعل، لا توقف البث!
    if StreamController.is_joining(chat_id) or chat_id in StreamController.active_calls:
        return
    if not await is_active_chat(chat_id):
        await _clear_(chat_id)


@app.on_message(filters.video_chat_ended, group=close)
async def on_vc_ended(_, message: Message):
    chat_id = message.chat.id
    if StreamController.is_joining(chat_id):
        return
    # تنظيف الطابور ومغادرة المساعد بدون إرسال طلب إغلاق مزدوج
    await StreamController.stop_stream(chat_id)
