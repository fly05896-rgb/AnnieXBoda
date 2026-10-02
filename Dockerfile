# نستخدم بايثون 3.11 لأنه الأكثر استقراراً وتوافقاً مع مكتبات البوتات والذكاء الاصطناعي
FROM python:3.12

# تحديث النظام وتثبيت الحزم الأساسية وأهمها ffmpeg المطلوب لتشغيل pytgcalls
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# تعيين مسار العمل داخل الحاوية
WORKDIR /app

# نسخ ملف المتطلبات أولاً (للاستفادة من الكاش وتسريع الرفع في المرات القادمة)
COPY requirements.txt .

# تحديث pip وتثبيت المكتبات، مع فرض تثبيت أحدث إصدار من pytgcalls مباشرة
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir pytgcalls --upgrade && \
    pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع إلى الحاوية
COPY . .

# أمر تشغيل البوت (قم بتغيير main.py إذا كان ملف التشغيل الرئيسي لديك باسم مختلف)
CMD ["python", "main.py"]
