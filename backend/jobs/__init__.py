"""
job_manager.py
---------------
Tiny in-memory job queue (no Redis/Celery needed for an MVP). Each job runs
the full pipeline in a background thread and reports progress that the
frontend polls.

Supports two input sources, both converging on the same scoring/clipping
pipeline once a transcript exists:
  - "youtube": paste a link -> yt-dlp downloads it, transcript comes from
    YouTube's own captions (may be blocked by YouTube's bot detection).
  - "upload":  upload a video file directly -> no download needed, transcript
    is generated locally and for free with faster-whisper speech-to-text.
"""

import os
import shutil
import threading
import time
import traceback
import uuid

from services import transcript_service, transcription_service, video_service, caption_service, scorer

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")

# Uploaded videos are transcribed locally on (likely) modest free-tier
# hardware, so we cap how long a video can be to keep processing time and
# memory usage reasonable. Raise this if you move to a beefier instance.
MAX_UPLOAD_DURATION_SECONDS = 10 * 60  # 10 minutes

PIPELINE_STEPS = [
    "fetching_video",
    "extracting_transcript",
    "detecting_moments",
    "generating_clips",
    "done",
]

_jobs = {}
_lock = threading.Lock()


def _new_job_record(source_type, youtube_url=None, original_filename=None):
    return {
        "id": None,
        "source_type": source_type,   # "youtube" | "upload"
        "youtube_url": youtube_url,
        "original_filename": original_filename,
        "status": "queued",       # queued | processing | done | error
        "step": None,
        "progress": 0,            # 0-100
        "clip_progress": {"current": 0, "total": 0},
        "error": None,
        "video_title": None,
        "video_duration": None,
        "transcript_source": None,
        "clips": [],
        "created_at": time.time(),
    }


def create_job(youtube_url: str) -> str:
    """Start a job sourced from a pasted YouTube link."""
    record = _new_job_record("youtube", youtube_url=youtube_url)
    job_id = uuid.uuid4().hex[:12]
    record["id"] = job_id

    with _lock:
        _jobs[job_id] = record

    job_dir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    thread = threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def create_job_from_upload(tmp_uploaded_path: str, original_filename: str) -> str:
    """
    Start a job sourced from an uploaded video file. tmp_uploaded_path is
    wherever Flask saved the incoming file (e.g. via a temp/upload dir);
    this function moves it into the job's own storage folder.
    """
    record = _new_job_record("upload", original_filename=original_filename)
    job_id = uuid.uuid4().hex[:12]
    record["id"] = job_id

    job_dir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    ext = os.path.splitext(original_filename or "")[1] or ".mp4"
    dest_path = os.path.join(job_dir, f"uploaded_source{ext}")
    # shutil.move (not os.replace) because the upload temp file and this
    # storage folder can live on different filesystems inside the
    # container; os.replace/os.rename can't cross filesystems and would
    # crash with "Invalid cross-device link" — shutil.move handles that
    # automatically by copying then removing the original when needed.
    shutil.move(tmp_uploaded_path, dest_path)

    with _lock:
        _jobs[job_id] = record

    thread = threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _update(job_id, **kwargs):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job_dir(job_id: str) -> str:
    return os.path.join(STORAGE_DIR, job_id)


# kept as an alias for internal use within this module
_job_dir = get_job_dir


def _find_uploaded_source(job_dir: str) -> str:
    for name in os.listdir(job_dir):
        if name.startswith("uploaded_source"):
            return os.path.join(job_dir, name)
    raise RuntimeError("Uploaded video file could not be found.")


def _score_and_generate_clips(job_id: str, job_dir: str, source_path: str, transcript: list):
    """Shared by both pipelines once a transcript + source video exist."""
    _update(job_id, step="detecting_moments", progress=50)
    top_clips = scorer.select_top_clips(transcript, top_n=10)
    if not top_clips:
        raise RuntimeError("No clip-worthy moments were found in this video's transcript.")
    _update(job_id, progress=55, clip_progress={"current": 0, "total": len(top_clips)})

    _update(job_id, step="generating_clips")
    clip_results = []
    total = len(top_clips)

    for idx, clip in enumerate(top_clips, start=1):
        clip_id = f"clip_{idx}"
        clip_mp4 = os.path.join(job_dir, f"{clip_id}.mp4")
        clip_srt = os.path.join(job_dir, f"{clip_id}.srt")
        clip_thumb = os.path.join(job_dir, f"{clip_id}.jpg")

        caption_service.write_srt_for_clip(transcript, clip["start"], clip["end"], clip_srt)
        # Clips are cut clean (no burned-in captions) so the frontend can offer
        # a genuine captions ON/OFF toggle using the .srt file as a soft overlay.
        video_service.cut_clip(source_path, clip["start"], clip["end"], clip_mp4)
        video_service.generate_thumbnail(clip_mp4, clip_thumb, at_second=min(1.0, clip["duration"] / 2))

        clip_results.append({
            "id": clip_id,
            "title": clip["title"],
            "start": clip["start"],
            "end": clip["end"],
            "duration": clip["duration"],
            "score": clip["score"],
            "label": clip["label"],
            "reasons": clip["reasons"],
            "signals": clip["signals"],
            "text": clip["text"],
            "video_file": f"{clip_id}.mp4",
            "srt_file": f"{clip_id}.srt",
            "thumbnail_file": f"{clip_id}.jpg",
        })

        progress = 55 + int((idx / total) * 40)
        _update(job_id, progress=progress, clips=clip_results,
                clip_progress={"current": idx, "total": total})

    _update(job_id, status="done", step="done", progress=100, clips=clip_results)


def _run_youtube_pipeline(job_id: str, job_dir: str, youtube_url: str):
    # 1. Fetch video metadata + download source video
    _update(job_id, status="processing", step="fetching_video", progress=5)
    info = video_service.get_video_info(youtube_url)
    _update(job_id, video_title=info.get("title"), video_duration=info.get("duration"))

    source_path = video_service.download_source_video(youtube_url, job_dir)
    _update(job_id, progress=25)

    # 2. Extract transcript (YouTube captions)
    _update(job_id, step="extracting_transcript", progress=30)
    transcript, source = transcript_service.get_transcript(youtube_url, work_dir=job_dir)
    _update(job_id, transcript_source=source, progress=45)

    _score_and_generate_clips(job_id, job_dir, source_path, transcript)


def _run_upload_pipeline(job_id: str, job_dir: str):
    # 1. Read basic info from the uploaded file (already on disk, no download needed)
    _update(job_id, status="processing", step="fetching_video", progress=10)
    source_path = _find_uploaded_source(job_dir)
    info = video_service.get_local_video_info(source_path)
    duration = info.get("duration")

    if duration and duration > MAX_UPLOAD_DURATION_SECONDS:
        minutes = MAX_UPLOAD_DURATION_SECONDS // 60
        raise RuntimeError(
            f"This video is too long for the free-tier upload path "
            f"(limit is {minutes} minutes). Try a shorter clip."
        )

    job = get_job(job_id)
    _update(job_id, video_title=job.get("original_filename") or "Uploaded video",
            video_duration=duration, progress=25)

    # 2. Generate transcript locally with free speech-to-text (no network calls)
    _update(job_id, step="extracting_transcript", progress=30)
    transcript = transcription_service.transcribe_video(source_path)
    _update(job_id, transcript_source="local-speech-to-text", progress=45)

    _score_and_generate_clips(job_id, job_dir, source_path, transcript)


def _run_pipeline(job_id: str):
    job_dir = _job_dir(job_id)
    job = get_job(job_id)

    try:
        if job["source_type"] == "upload":
            _run_upload_pipeline(job_id, job_dir)
        else:
            _run_youtube_pipeline(job_id, job_dir, job["youtube_url"])

    except Exception as exc:
        _update(job_id, status="error", error=str(exc))
        traceback.print_exc()
