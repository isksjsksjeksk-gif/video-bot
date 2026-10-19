FROM python:3.11-slim

# প্রয়োজনীয় সিস্টেম প্যাকেজ এবং FFmpeg ইনস্টল
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# পাইথন লাইব্রেরি ইনস্টলেশন
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# সোর্স কোড কপি
COPY . .

# Render ডিফল্ট পোর্ট
EXPOSE 10000

CMD ["python", "bot.py"]
