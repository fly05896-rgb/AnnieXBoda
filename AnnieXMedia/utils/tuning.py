import asyncio
import os

# 🔥 Fly.io Performance-CPU-10x (66.5 GB RAM) + Py-TgCalls v4.0 Passthrough Mode
# نستخدم كل الكورات المتاحة (10 Performance Cores) + 4 Threads إضافية لضمان عدم توقف الـ I/O
CPU = (os.cpu_count() or 10) + 4

# التوازن الذهبي لسيرفر 10x Performance CPU + Opus/Video Passthrough
MAX_CONCURRENT = 10

# 🚀 THE SPACE CHUNK (10MB):
# 10 * 1024 * 1024 = 10,485,760 bytes
CHUNK_SIZE = 10485760

# ⏳ Timeouts
YTDLP_TIMEOUT = 300

# 🧠 Meta Cache (الذاكرة — السيرفر يملك 66GB RAM)
YOUTUBE_META_TTL = 1800  # 30 دقيقة
YOUTUBE_META_MAX = 25000  # يحفظ 25 ألف رابط في الرام

# السيمفور (شرطي المرور)
SEM = asyncio.Semaphore(MAX_CONCURRENT)
