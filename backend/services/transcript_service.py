"""
transcript_service.py
----------------------
Extracts a timestamped transcript for a YouTube video using only free
methods:

  1. youtube-transcript-api (fastest, no video download needed) — uses
     YouTube's own caption tracks (manual or auto-generated).
  2. yt-dlp caption download (.vtt) as a fallback if (1) fails or the
     video has captions disabled for that library but available via
     yt-dlp's extractor.

Returns a list of: {"start": float, "duration": float, "text": str}
"""

import os
import re
import glob

from services.video_service import extract_video_id


def _get_transcript_via_api(video_id: str):
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)

    fetched = None
    try:
        fetched = transcript_list.find_transcript(["en"]).fetch()
    except Exception:
        # fall back to whatever transcript exists (auto-translate to English if possible)
        for t in transcript_list:
            try:
                if t.is_translatable:
                    fetched = t.translate("en").fetch()
                else:
                    fetched = t.fetch()
                break
            except Exception:
                continue

    if fetched is None:
        raise RuntimeError("No transcript available via youtube-transcript-api")

    entries = []
    for snippet in fetched:
        # snippet supports both attribute and dict-style access depending on version
        text = getattr(snippet, "text", None) or snippet.get("text", "")
        start = getattr(snippet, "start", None)
        if start is None:
            start = snippet.get("start", 0.0)
        duration = getattr(snippet, "duration", None)
        if duration is None:
            duration = snippet.get("duration", 1.0)
        entries.append({"start": float(start), "duration": float(duration), "text": text})

    return entries


def _parse_vtt(vtt_path: str):
    """Minimal WebVTT parser -> list of {start, duration, text}."""
    timestamp_re = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
    )

    def to_seconds(ts: str) -> float:
        parts = ts.split(":")
        if len(parts) == 3:
            h, m, s = parts
        else:
            h, m, s = "0", parts[0], parts[1]
        return int(h) * 3600 + int(m) * 60 + float(s)

    entries = []
    with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    i = 0
    while i < len(lines):
        match = timestamp_re.search(lines[i])
        if match:
            start = to_seconds(match.group(1))
            end = to_seconds(match.group(2))
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip() and not timestamp_re.search(lines[i]):
                clean = re.sub(r"<[^>]+>", "", lines[i]).strip()
                if clean:
                    text_lines.append(clean)
                i += 1
            text = " ".join(text_lines).strip()
            if text:
                entries.append({"start": start, "duration": max(end - start, 0.1), "text": text})
        else:
            i += 1

    # de-duplicate consecutive identical/overlapping caption lines (common in auto-captions)
    deduped = []
    for entry in entries:
        if deduped and deduped[-1]["text"] == entry["text"]:
            deduped[-1]["duration"] = (entry["start"] + entry["duration"]) - deduped[-1]["start"]
            continue
        deduped.append(entry)

    return deduped


def _get_transcript_via_ytdlp(youtube_url: str, work_dir: str):
    import yt_dlp

    os.makedirs(work_dir, exist_ok=True)
    out_template = os.path.join(work_dir, "captions")

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-orig"],
        "subtitlesformat": "vtt",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    vtt_files = glob.glob(out_template + "*.vtt")
    if not vtt_files:
        raise RuntimeError("yt-dlp did not produce any caption file for this video.")

    # prefer a plain English file over auto-generated if both exist
    vtt_files.sort(key=lambda p: ("auto" in p.lower(), p))
    return _parse_vtt(vtt_files[0])


def get_transcript(youtube_url: str, work_dir: str = "/tmp"):
    """
    Returns (transcript, source) where source is "youtube-transcript-api"
    or "yt-dlp-captions", whichever succeeded.
    Raises RuntimeError if no free method produces a transcript (e.g.
    captions are fully disabled on that video).
    """
    video_id = extract_video_id(youtube_url)

    try:
        transcript = _get_transcript_via_api(video_id)
        if transcript:
            return transcript, "youtube-transcript-api"
    except Exception:
        pass

    try:
        transcript = _get_transcript_via_ytdlp(youtube_url, work_dir)
        if transcript:
            return transcript, "yt-dlp-captions"
    except Exception as exc:
        raise RuntimeError(
            "Could not extract a transcript with any free method. "
            "This video may have captions disabled. "
            f"(last error: {exc})"
        )

    raise RuntimeError("No transcript could be extracted for this video.")
