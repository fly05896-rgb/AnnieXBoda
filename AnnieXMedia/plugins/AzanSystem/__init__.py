# System: Azan Maestro (Enterprise Edition)
# File Path: AnnieXMedia/plugins/AzanSystem/__init__.py

from AnnieXMedia import app

# 1. استيراد الملفات لتسجيل الأوامر تلقائياً
from . import az_conf
from . import az_utils
from . import az_admin
from . import az_athkar
from . import az_quran
from . import az_broadcast

# 2. استيراد أدوات الجدولة
from .az_utils import scheduler, init_azan_scheduler
from .az_broadcast import init_broadcast_schedule

# 3. تفعيل مجدول الأذان ومجدول النشر التلقائي فور تشغيل البوت بدون انتظار أول رسالة
try:
    init_broadcast_schedule(scheduler)
    init_azan_scheduler()
    print("[Azan System] Integrated & Scheduler Started Successfully.")
except Exception as e:
    print(f"[Azan System] Scheduler Init Error: {e}")
