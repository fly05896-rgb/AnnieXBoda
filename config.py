# ── 𝚂ᴏᴜʀᴄᴇ ✘ 𝐁ᴏᴅᴀ © 2026 ──────────────────────────────────────────────────────
# Modified by: MusicBoda & TitanOS Core
# Optimized for 16-Cores & 88GB RAM Performance | AI INTEGRATED

import re
import sys
import os
from os import getenv
from dotenv import load_dotenv
from pyrogram import filters

# Load environment variables
load_dotenv()

# ── Core bot config (إعدادات البوت الأساسية) ───────────────────────────────────
try:
    API_ID = int(getenv("API_ID"))
    API_HASH = getenv("API_HASH")
except (TypeError, ValueError):
    print("🚫 خطأ: يجب وضع API_ID و API_HASH في متغيرات النظام ليعمل البوت.")
    sys.exit()

BOT_TOKEN = getenv("BOT_TOKEN")

# معلومات المالك
OWNER_ID = int(getenv("OWNER_ID", 8313557781))
OWNER_USERNAME = getenv("OWNER_USERNAME", "𝐁ᴏᴅᴀ˼")

# معلومات البوت والمساعد
BOT_USERNAME = getenv("BOT_USERNAME", "Boda")
BOT_NAME = getenv("BOT_NAME", "˹𝚂ᴏᴜʀᴄᴇ ✘ 𝐁ᴏᴅᴀ˼ ♪")
ASSUSERNAME = getenv("ASSUSERNAME", "S_G0C7")

# ── 🔥 المسارات النووية (Nuclear Paths) 🔥 ──────────────────────────────────
DOWNLOAD_PATH = "/dev/shm/AnnieDownloads"
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

AUTO_DOWNLOADS_CLEAR = getenv("AUTO_DOWNLOADS_CLEAR", "True")

# ── Database & logging ─────────────────────────────────────────────────────────
MONGO_DB_URI = getenv("MONGO_DB_URI")
LOGGER_ID = int(getenv("LOGGER_ID", -1004295349964))

# ── Limits & AI Groups ────────────────────────────────────────────────────────
DURATION_LIMIT_MIN = int(getenv("DURATION_LIMIT", 600)) 
SONG_DOWNLOAD_DURATION = int(getenv("SONG_DOWNLOAD_DURATION", "3600"))
SONG_DOWNLOAD_DURATION_LIMIT = int(getenv("SONG_DOWNLOAD_DURATION_LIMIT", "5400"))
TG_AUDIO_FILESIZE_LIMIT = int(getenv("TG_AUDIO_FILESIZE_LIMIT", "2147483648")) 
TG_VIDEO_FILESIZE_LIMIT = int(getenv("TG_VIDEO_FILESIZE_LIMIT", "2147483648")) 
PLAYLIST_FETCH_LIMIT = int(getenv("PLAYLIST_FETCH_LIMIT", "100"))

# مجموعة معالجة الذكاء الاصطناعي (لتجنب التعارض)
AI_HANDLER_GROUP = int(getenv("AI_HANDLER_GROUP", 30))

#سيرفر / مستخدم
SERVER_PLAYLIST_LIMIT = int(getenv("SERVER_PLAYLIST_LIMIT", 30))
# ── External APIs ──────────────────────────────────────────────────────────────
COOKIE_URL = getenv("COOKIE_URL")
API_URL = "https://hyperionengine.fly.dev"
VIDEO_API_URL = "https://hyperionengine.fly.dev"
API_KEY = getenv("API_KEY")
DEEP_API = getenv("DEEP_API")

# مفتاح الذكاء الاصطناعي الرسمي (GPT-4) من السكرتس
AI_API_KEY = getenv("GPT_4")

# ── Hosting / deployment ───────────────────────────────────────────────────────
HEROKU_APP_NAME = getenv("HEROKU_APP_NAME")
HEROKU_API_KEY = getenv("HEROKU_API_KEY")

# ── Git / updates ──────────────────────────────────────────────────────────────
UPSTREAM_REPO = getenv("UPSTREAM_REPO", "https://t.me/SourceBoda")
UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "Master")
GIT_TOKEN = getenv("GIT_TOKEN")

# ── Support links ──────────────────────────────────────────────────────────────
SUPPORT_CHANNEL = getenv("SUPPORT_CHANNEL", "https://t.me/SourceBoda")
SUPPORT_CHAT = getenv("SUPPORT_CHAT", "https://t.me/music0587")

# ── Assistant auto-leave ───────────────────────────────────────────────────────
AUTO_LEAVING_ASSISTANT = getenv("AUTO_LEAVING_ASSISTANT", "False")
AUTO_LEAVE_ASSISTANT_TIME = int(getenv("ASSISTANT_LEAVE_TIME", "3600"))

# ── Debug ──────────────────────────────────────────────────────────────────────
DEBUG_IGNORE_LOG = True

# ── Spotify (optional) ─────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID", "22b6125bfe224587b722d6815002db2b")
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET", "c9c63c6fbf2f467c8bc68624851e9773")

# ── Session strings ────────────────────────────────────────────────────────────
STRING1 = getenv("STRING_SESSION")
STRING2 = getenv("STRING_SESSION2")
STRING3 = getenv("STRING_SESSION3")
STRING4 = getenv("STRING_SESSION4")
STRING5 = getenv("STRING_SESSION5")

# ── Media assets ───────────────────────────────────────────────────────────────
START_VIDS = [
    "https://files.catbox.moe/b6533n.jpg",
    "https://files.catbox.moe/wqipfn.jpg",
    "https://files.catbox.moe/efzuds.jpg",
]

STICKERS = [
    "CAACAgQAAyEFAATHCHTJAAIToGlfMcgnOpNnuYnm1hlBTW_pZgZfAAIfFgAC-CS4UbtZNHZyyA3BHgQ",
    "CAACAgUAAyEFAATHCHTJAAITn2lfMb5VpY0QAom50knojYHju4bTAAILFQAC-vEZVMBmWHCQ-sJuHgQ",
]

UNIFIED_IMG = "https://files.catbox.moe/ompn1o.jpg"

START_IMG_URL = HELP_IMG_URL = PING_VID_URL = PLAYLIST_IMG_URL = UNIFIED_IMG
STATS_VID_URL = TELEGRAM_AUDIO_URL = TELEGRAM_VIDEO_URL = UNIFIED_IMG
STREAM_IMG_URL = SOUNCLOUD_IMG_URL = YOUTUBE_IMG_URL = UNIFIED_IMG
SPOTIFY_ARTIST_IMG_URL = SPOTIFY_ALBUM_IMG_URL = SPOTIFY_PLAYLIST_IMG_URL = UNIFIED_IMG

# ── Helpers ────────────────────────────────────────────────────────────────────
def time_to_seconds(time: str) -> int:
    try:
        return sum(int(x) * 60**i for i, x in enumerate(reversed(time.split(":"))))
    except:
        return 0

DURATION_LIMIT = time_to_seconds(f"{DURATION_LIMIT_MIN}:00")

# ───── نصوص التشغيل والتحميل ───── #
AYU = [
    "جـاري الـتـشـغـيـل .. 🤍",
    "جـاري الـتـحـمـيـل .. 🫶",
    "لـحـظـة مـن فـضـلـك .. 🤍",
    "طـلـبـك قـيـد الـتـنـفـيـذ .. ☔",
    "يـتـم تـشـغـيـل الـتـراك .. 💝"
]

# ───── رسالة الستارت ───── #
AYUV = [
    """
صـلـي عـلـي الـنـبـي وتـبـسـم 🤍🌿.
مـرحـبـا انـا بـوت تـشـغـيـل صوتيات متطور ☔
أهـلاً بـك عـزيـزي {0} 🫶
وظـيـفـتـي تـشـغـيـل الـمـيـديـا فـي الـمـكـالـمـات بـجـودة عـالـيـة وبـدون تـقـطـيـع 🤍.
⧉ لـتـشـغـيـل أغـنـيـة اكـتـب « تشغيل + اسم الاغنية »
⧉ لـلـتـحـكـم فـي الـبـوت اضـغـط « الأوامـر » بـالأسـفـل 💝.
ـــــــــــــــــــــــــــــــــــــــــــــــــــــــ
⧉ وقـت الـعـمـل : {2}
⧉ الـرام : {5}
ـــــــــــــــــــــــــــــــــــــــــــــــــــــــ
    """,
    """
صـلـي عـلـي الـنـبـي وتـبـسـم 🤍🌿.
مـرحـبـا انـا بـوت تـشـغـيـل صوتيات متطور ☔
أهـلاً بـكـم فـي {0} 🫶
أنـا {1} .. جـاهـز لـخـدمـتـكـم 🤍.
⧉ أعـمـل بـكـفـاءة عـالـيـة بـدون تـوقـف.
⧉ فـقـط أضـفـنـي لـمـجـمـوعـتـك وارفـعـنـي مـشـرف 💝.
⧉ وقـت الـتـشـغـيـل : {2}
    """
]

# ── Runtime structures ─────────────────────────────────────────────────────────
BANNED_USERS = filters.user()
adminlist = {}
lyrical = {}
autoclean = []
confirmer = {}

# ── Minimal validation ─────────────────────────────────────────────────────────
if SUPPORT_CHANNEL and not re.match(r"^https?://", SUPPORT_CHANNEL):
    raise SystemExit("[ERROR] - Invalid SUPPORT_CHANNEL URL.")

if SUPPORT_CHAT and not re.match(r"^https?://", SUPPORT_CHAT):
    raise SystemExit("[ERROR] - Invalid SUPPORT_CHAT URL.")
