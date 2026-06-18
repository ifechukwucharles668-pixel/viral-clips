"""
scorer.py
---------
Rule-based "viral moment" detection engine.

No ML, no paid APIs. Pure heuristics over a transcript (list of timestamped
caption entries) to find candidate clip windows and rank them by an
engagement score. This mirrors the kind of signal Opus Clip-style tools use,
simplified to transparent, explainable rules.
"""

import re

# ---------------------------------------------------------------------------
# Keyword / phrase banks (all lowercase, matched as substrings)
# ---------------------------------------------------------------------------

EMOTIONAL_WORDS = [
    "wow", "crazy", "insane", "shocking", "secret", "important", "unbelievable",
    "amazing", "terrifying", "incredible", "mind-blowing", "mind blowing",
    "scary", "brutal", "devastating", "outrageous", "huge", "massive",
    "ridiculous", "wild", "horrifying", "stunning", "epic", "disaster",
    "nightmare", "genius", "obsessed", "furious", "heartbreaking",
]

HOOK_PHRASES = [
    "did you know", "what if", "imagine if", "here's why", "here is why",
    "this is why", "the truth is", "nobody tells you", "no one tells you",
    "i bet you didn't know", "you won't believe", "wait for it",
    "the real reason", "what nobody talks about", "here's the secret",
]

ATTENTION_PHRASES = [
    "listen", "boom", "watch this", "pay attention", "here's the thing",
    "let me explain", "check this out", "look at this", "this is huge",
    "stop scrolling", "hold on",
]

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MIN_CLIP_DURATION = 15      # seconds
MAX_CLIP_DURATION = 45      # seconds
MIN_GAP_BETWEEN_CLIPS = 4   # seconds, avoid two clips overlapping/touching
SCORE_HIGH_THRESHOLD = 10
SCORE_MEDIUM_THRESHOLD = 5


def _normalize_text(entries):
    return " ".join(e["text"].strip() for e in entries if e.get("text")).lower()


def build_candidate_windows(transcript, min_duration=MIN_CLIP_DURATION,
                             max_duration=MAX_CLIP_DURATION, entry_stride=2):
    """
    Slide over the transcript and build every plausible clip window between
    min_duration and max_duration seconds long.

    transcript: list of {"start": float, "duration": float, "text": str},
                sorted ascending by start time.
    entry_stride: only start a new window every N entries, to bound the
                  number of candidates on long transcripts.
    """
    candidates = []
    n = len(transcript)

    for i in range(0, n, entry_stride):
        start_time = transcript[i]["start"]
        window_entries = []

        for j in range(i, n):
            entry = transcript[j]
            end_time = entry["start"] + entry["duration"]
            duration = end_time - start_time

            if duration > max_duration:
                break

            window_entries.append(entry)

            if duration >= min_duration:
                candidates.append({
                    "start": start_time,
                    "end": end_time,
                    "entries": list(window_entries),
                })

    return candidates


def score_window(window):
    """
    Returns (score: int, reasons: list[str], signals: dict) for one
    candidate window.
    """
    text = _normalize_text(window["entries"])
    score = 0
    reasons = []
    signals = {
        "emotional_words": [],
        "hook_phrase": None,
        "attention_phrases": [],
        "has_question": False,
        "short_punchy_count": 0,
    }

    found_emotional = [w for w in EMOTIONAL_WORDS if w in text]
    if found_emotional:
        capped = found_emotional[:3]
        score += len(capped) * 3
        signals["emotional_words"] = capped
        reasons.append(f"Emotional language: {', '.join(capped)}")

    found_hook = next((p for p in HOOK_PHRASES if p in text), None)
    if found_hook:
        score += 4
        signals["hook_phrase"] = found_hook
        reasons.append(f'Hook detected: "{found_hook}"')

    found_attention = [p for p in ATTENTION_PHRASES if p in text]
    if found_attention:
        capped = found_attention[:2]
        score += len(capped) * 2
        signals["attention_phrases"] = capped
        reasons.append(f"Attention grabber: \"{capped[0]}\"")

    if "?" in text:
        score += 2
        signals["has_question"] = True
        reasons.append("Contains a question (curiosity hook)")

    rough_sentences = re.split(r"[.!?]", text)
    short_punchy = [s.strip() for s in rough_sentences if 0 < len(s.strip().split()) <= 7]
    if short_punchy:
        bonus = min(len(short_punchy), 3)
        score += bonus
        signals["short_punchy_count"] = len(short_punchy)
        reasons.append(f"{len(short_punchy)} short, punchy sentence(s)")

    return score, reasons, signals


def _label_for_score(score):
    if score >= SCORE_HIGH_THRESHOLD:
        return "High"
    if score >= SCORE_MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def _make_title(text, max_words=10):
    words = text.strip().split()
    snippet = " ".join(words[:max_words])
    snippet = snippet.strip(" ,.;:")
    if not snippet:
        return "Untitled moment"
    return snippet[0].upper() + snippet[1:] + ("…" if len(words) > max_words else "")


def _windows_overlap(a, b, min_gap=MIN_GAP_BETWEEN_CLIPS):
    return not (a["end"] + min_gap <= b["start"] or a["start"] >= b["end"] + min_gap)


def select_top_clips(transcript, top_n=10):
    """
    Full pipeline: build candidate windows, score them, greedily pick the
    top non-overlapping windows by score.

    Returns a list of clip dicts:
      {
        "start": float, "end": float, "duration": float,
        "score": int, "label": "High"|"Medium"|"Low",
        "title": str, "text": str, "reasons": [str], "signals": {...}
      }
    """
    if not transcript:
        return []

    candidates = build_candidate_windows(transcript)
    scored = []
    for window in candidates:
        score, reasons, signals = score_window(window)
        scored.append((score, reasons, signals, window))

    # Highest score first; for ties prefer the shorter, tighter clip
    scored.sort(key=lambda x: (-x[0], x[3]["end"] - x[3]["start"]))

    selected = []
    for score, reasons, signals, window in scored:
        if any(_windows_overlap(window, s["_window"]) for s in selected):
            continue

        text = _normalize_text(window["entries"])
        selected.append({
            "start": round(window["start"], 2),
            "end": round(window["end"], 2),
            "duration": round(window["end"] - window["start"], 2),
            "score": score,
            "label": _label_for_score(score),
            "title": _make_title(text),
            "text": text,
            "reasons": reasons if reasons else ["Selected as a representative segment of the video"],
            "signals": signals,
            "_window": window,  # internal use only, stripped before returning
        })

        if len(selected) >= top_n:
            break

    selected.sort(key=lambda c: c["start"])

    for c in selected:
        c.pop("_window", None)

    return selected
