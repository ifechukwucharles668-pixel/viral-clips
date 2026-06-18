"""
caption_service.py
-------------------
Builds .srt subtitle files for a clip from the full-video transcript,
re-timed so 00:00:00,000 lines up with the start of the clip.
"""


def _format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt_for_clip(transcript, clip_start, clip_end, max_chars_per_line=42):
    """
    transcript: full-video list of {"start","duration","text"}
    clip_start/clip_end: absolute seconds in the source video
    Returns: srt file content as a string
    """
    lines = []
    index = 1

    for entry in transcript:
        entry_start = entry["start"]
        entry_end = entry["start"] + entry["duration"]

        # keep entries that intersect the clip window at all
        if entry_end <= clip_start or entry_start >= clip_end:
            continue

        rel_start = max(entry_start, clip_start) - clip_start
        rel_end = min(entry_end, clip_end) - clip_start
        if rel_end <= rel_start:
            continue

        text = entry["text"].strip().replace("\n", " ")
        if not text:
            continue

        # simple word-wrap so burned-in captions don't run off screen
        wrapped = []
        current = ""
        for word in text.split():
            candidate = (current + " " + word).strip()
            if len(candidate) > max_chars_per_line and current:
                wrapped.append(current)
                current = word
            else:
                current = candidate
        if current:
            wrapped.append(current)
        text = "\n".join(wrapped)

        lines.append(
            f"{index}\n"
            f"{_format_srt_timestamp(rel_start)} --> {_format_srt_timestamp(rel_end)}\n"
            f"{text}\n"
        )
        index += 1

    return "\n".join(lines)


def write_srt_for_clip(transcript, clip_start, clip_end, out_path):
    content = build_srt_for_clip(transcript, clip_start, clip_end)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path
