from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yt_dlp

@dataclass
class DownloadResult:
    filepath: Path
    title: str
    filesize: int

YT_OPTS_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "merge_output_format": "mp4",
    "restrictfilenames": True,
}

def download_video(url, download_dir):
    ydl_opts = {
        'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
        'outtmpl': str(download_dir / '%(title)s.%(ext)s'),
        'ffmpeg_location': r'C:\Users\semen\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe',  # Добавьте эту строку
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
        if not filename.exists():
            filename = Path(ydl.prepare_filename(info))
        title = info.get("title") or "video"
        size = filename.stat().st_size
        return DownloadResult(filepath=filename, title=title, filesize=size)