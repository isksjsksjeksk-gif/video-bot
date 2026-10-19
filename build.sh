#!/usr/bin/env bash
set -e

echo "📦 পাইথন ডিপেনডেন্সি ইনস্টল হচ্ছে..."
pip install --upgrade pip
pip install -r requirements.txt

# স্ট্যাটিক FFmpeg ও FFprobe ডাউনলোড (যদি পূর্বে না থাকে)
if [ ! -f "./ffmpeg" ]; then
    echo "⬇️ FFmpeg ডাউনলোড হচ্ছে..."
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o ffmpeg.tar.xz
    tar -xf ffmpeg.tar.xz --wildcards '*/ffmpeg' '*/ffprobe' --strip-components=1
    chmod +x ffmpeg ffprobe
    rm -f ffmpeg.tar.xz
    echo "✅ FFmpeg প্রস্তুত!"
fi
