# arma - Advanced Resource Media Assistant
## 🚀 arMa Overview
arma is a powerful CLI tool that supports:
- ✅ M3U8 (HLS) stream downloads
- ✅ DASH (MPD) manifest handling
- ✅ Direct file downloads with resume support
- ✅ Quality selection for adaptive streams
- ✅ Subtitle track downloading (HLS)
- ✅ Automatic retries with exponential backoff
- ✅ Parallel segment downloads
- ✅ Clean progress bars and intelligent naming
---
### 🧠 Features
- Resumes interrupted downloads
- Downloads M3U8/DASH streams and direct URLs
- Multi-threaded segment downloader
- Supports HLS master playlists with quality choice
- Downloads subtitles if available
- Automatically renames duplicate filenames
- Smart and colorful progress bars
- Banner branding on launch
## 📦 Installation

### Clone the repo

```bash
git clone https://github.com/yourusername/arma.git
cd arma
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Requirements
Python 3.7+
- ffmpeg installed and in your system path
Install ffmpeg:
- Debian/Ubuntu: sudo apt install ffmpeg
- macOS (Homebrew): brew install ffmpeg
- Windows: https://ffmpeg.org/download.html
###  🧪 Usage
```bash
python arma/arMa.py --url <url> [--name <file>] [--dir <directory>] [--parallel <threads>]
```

Created with ❤️ by **Your Name**  
GitHub: [@arnobmonir](https://github.com/arnobmonir)
