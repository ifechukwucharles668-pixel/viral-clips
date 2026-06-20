"""
video_service.py
-----------------
All interaction with YouTube (via yt-dlp) and FFmpeg.
No paid APIs. yt-dlp handles video download + caption download for free.
"""

import os
import re
import subprocess

# Optional: path to a cookies.txt file (Netscape format) exported from a
# logged-in browser session. Not required, but if YouTube starts blocking
# requests with "Sign in to confirm you're not a bot", setting the
# COOKIES_FILE environment variable to point at an uploaded cookies file
# is the most effective fix. Safe to leave unset.
COOKIES_FILE = os.environ.get("COOKIES_FILE")

# Trying a few different "player clients" makes requests look like they're
# coming from the YouTube mobile apps rather than a script, which is less
# likely to get flagged as a bot.
_PLAYER_CLIENTS = ["android", "ios", "web"]


def _base_ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": _PLAYER_CLIENTS}},
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    if extra:
        opts.update(extra)
    return opts


def extract_video_id(youtube_url: str) -> str:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:&|$|\?|\/)",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    raise ValueError("Could not extract a YouTube video ID from that URL.")


def get_video_info(youtube_url: str) -> dict:
    """Lightweight metadata fetch, no download."""
    import yt_dlp
    ydl_opts = _base_ydl_opts({"skip_download": True})
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
    }


def download_source_video(youtube_url: str, out_dir: str, filename: str = "source.mp4") -> str:
    """
    Downloads a single, reasonably sized mp4 (<=720p) to out_dir.
    Returns the absolute path to the downloaded file.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, filename)
    import yt_dlp

    ydl_opts = _base_ydl_opts({
        "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "noplaylist": True,
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    if not os.path.exists(out_path):
        # yt-dlp sometimes appends an extension even with an explicit outtmpl
        candidates = [f for f in os.listdir(out_dir) if f.startswith("source")]
        if candidates:
            out_path = os.path.join(out_dir, candidates[0])

    return out_path


def get_local_video_info(path: str) -> dict:
    """
    Reads basic info (currently just duration) from a local video file using
    ffprobe. Used for the "upload a video" path, where there's no YouTube
    metadata to ask for instead.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Could not read the uploaded video file: {result.stderr[-500:]}")

    try:
        duration = float(result.stdout.strip())
    except ValueError:
        duration = None

    return {"duration": duration}


def cut_clip(source_path: str, start: float, end: float, out_path: str,
             burn_subtitles_path: str = None) -> str:
    """
    Cuts [start, end] out of source_path using ffmpeg, re-encoding so the
    cut lands exactly on the requested boundaries (stream-copy cuts can only
    snap to keyframes). Optionally burns in subtitles from an .srt file.
    """
    duration = max(end - start, 0.5)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", source_path,
        "-t", f"{duration:.3f}",
    ]

    if burn_subtitles_path:
        # ffmpeg's subtitles filter needs an escaped path on most platforms
        escaped = burn_subtitles_path.replace("\\", "/").replace(":", "\\:")
        cmd += ["-vf", f"subtitles='{escaped}':force_style='FontSize=18,Outline=1,Bold=1'"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for clip {start}-{end}: {result.stderr[-2000:]}")

    return out_path


def generate_thumbnail(clip_path: str, out_path: str, at_second: float = 1.0) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{at_second:.2f}",
        "-i", clip_path,
        "-frames:v", "1",
        "-q:v", "3",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg thumbnail failed: {result.stderr[-1000:]}")
    return out_path
