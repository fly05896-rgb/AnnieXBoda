import copy
import os
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_DB_URI, OWNER_ID as CONFIG_OWNER_ID

# --- [ 1. إعداد المالك ] ---
try:
    MAIN_OWNER = int(os.getenv("OWNER_ID"))
except Exception:
    if isinstance(CONFIG_OWNER_ID, list):
        MAIN_OWNER = CONFIG_OWNER_ID[0]
    elif isinstance(CONFIG_OWNER_ID, int):
        MAIN_OWNER = CONFIG_OWNER_ID
    else:
        MAIN_OWNER = 0

DEVS = [MAIN_OWNER]
SECOND_DEV_ID = 8462240673
if SECOND_DEV_ID not in DEVS:
    DEVS.append(SECOND_DEV_ID)

AZAN_GROUP = 57

# --- [ 2. إعداد قاعدة البيانات ] ---
db_client = AsyncIOMotorClient(MONGO_DB_URI)
settings_db = db_client.BrandrdX.azan_final_pro_db
resources_db = db_client.BrandrdX.azan_resources_final_db
azan_logs_db = db_client.BrandrdX.admin_system_v3_db.azan_logs

# --- [ 3. الذاكرة المؤقتة ] ---
local_cache = {}
admin_state = {}

# --- [ 4. الثوابت والأسماء ] ---
PRAYER_NAMES_AR = {
    "Fajr": "الفجر",
    "Dhuhr": "الظهر",
    "Asr": "العصر",
    "Maghrib": "المغرب",
    "Isha": "العشاء",
}
PRAYER_NAMES_REV = {v: k for k, v in PRAYER_NAMES_AR.items()}

# --- [ 5. الأدعية ] ---
MORNING_DUAS = [
    "اللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور 🤍",
    "أصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له 💕",
    "اللهم إني أسألك خير هذا اليوم، فتحه، ونصره، ونوره، وبركته، وهداه 🤎",
    "رضيت بالله رباً، وبالإسلام ديناً، وبمحمد صلى الله عليه وسلم نبياً 🤍",
    "يا حي يا قيوم برحمتك أستغيث، أصلح لي شأني كله ولا تكلني إلى نفسي طرفة عين 🤎",
    "اللهم أنت ربي لا إله إلا أنت، خلقتني وأنا عبدك، وأنا على عهدك ووعدك ما استطعت ♥️",
    "اللهم إني أسألك علماً نافعاً، ورزقاً طيباً، وعملاً متقبلاً 💕",
    "بسم الله الذي لا يضر مع اسمه شيء في الأرض ولا في السماء وهو السميع العليم 💕",
    "اللهم عافني في بدني، اللهم عافني في سمعي، اللهم عافني في بصري 💞",
    "اللهم إني أسألك العفو والعافية في ديني ودنياي وأهلي ومالي 🤍",
    "أصبحنا على فطرة الإسلام، وعلى كلمة الإخلاص، وعلى دين نبينا محمد 💖",
    "اللهم اجعل صباحنا هذا صباحاً مباركاً، تفتح لنا فيه أبواب رحمتك 💖",
    "ربي أسألك في هذا الصباح أن تريح قلبي وفكري 💕",
    "حسبي الله لا إله إلا هو، عليه توكلت وهو رب العرش العظيم (7 مرات) 💕",
]

NIGHT_DUAS = [
    "اللهم بك أمسينا، وبك أصبحنا، وبك نحيا، وبك نموت، وإليك المصير 🤍",
    "أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له 🤍",
    "اللهم أنت ربي لا إله إلا أنت، خلقتني وأنا عبدك، وأنا على عهدك ووعدك ما استطعت 🤍",
    "اللهم إني أسألك العفو والعافية في الدنيا والآخرة 💖",
    "اللهم استر عوراتي وآمن روعاتي، اللهم احفظني من بين يدي ومن خلفي 🤍",
    "اللهم عافني في بدني، اللهم عافني في سمعي، اللهم عافني في بصري 💖",
    "اللهم إني أعوذ بك من الكفر والفقر، وأعوذ بك من عذاب القبر 🤍",
    "حسبي الله لا إله إلا هو عليه توكلت وهو رب العرش العظيم 💖",
    "بسم الله الذي لا يضر مع اسمه شيء في الأرض ولا في السماء 🤍",
    "يا حي يا قيوم برحمتك أستغيث، أصلح لي شأني كله ولا تكلني إلى نفسي طرفة عين 🤍",
    "أمسينا على فطرة الإسلام، وعلى كلمة الإخلاص، وعلى دين نبينا محمد 💖",
]

# --- [ 6. الموارد (الروابط والاستيكرات) ] ---
DEFAULT_RESOURCES = {
    "Fajr": {
        "name": "الفجر",
        "vidid": "r9AWBlpantg",
        "link": "https://youtu.be/watch?v=r9AWBlpantg",
        "sticker": "CAACAgQAAyEFAATHCHTJAAIJD2lOq8aLkRR49evBKiITWWhwtgEoAALoGgACp_FYUQuzqVH-JHS5HgQ",
    },
    "Dhuhr": {
        "name": "الظهر",
        "vidid": "21MuvFr7CK8",
        "link": "https://www.youtube.com/watch?v=21MuvFr7CK8",
        "sticker": "CAACAgQAAyEFAATHCHTJAAIJEWlOrFKzjSDZeWfl6U3F-lrKldRXAAJMGwACMVlYUa15CORC0p0xHgQ",
    },
    "Asr": {
        "name": "العصر",
        "vidid": "bb6cNncMdiM",
        "link": "https://www.youtube.com/watch?v=bb6cNncMdiM",
        "sticker": "CAACAgQAAyEFAATHCHTJAAIJE2lOrFRQIbcdLfnpdl5PtbdqNyR6AALFGQAC3ZZRUcK5YivXbwUAAR4E",
    },
    "Maghrib": {
        "name": "المغرب",
        "vidid": "hKPcNh7WHoM",
        "link": "https://youtu.be/watch?v=hKPcNh7WHoM",
        "sticker": "CAACAgQAAyEFAATHCHTJAAIJFWlOrFT4eOnPJDsSuU6Ya-V0WPQdAALfFwACcIVQUX6NcNNCxvdRHgQ",
    },
    "Isha": {
        "name": "العشاء",
        "vidid": "hKPcNh7WHoM",
        "link": "https://youtu.be/watch?v=hKPcNh7WHoM",
        "sticker": "CAACAgQAAyEFAATHCHTJAAIJF2lOrFVxhRGefHki3d4s-hLC9cKHAALqHAAC3oZQUWqQdvdwXnGLHgQ",
    },
}

CURRENT_RESOURCES = copy.deepcopy(DEFAULT_RESOURCES)
CURRENT_DUA_STICKER = None
