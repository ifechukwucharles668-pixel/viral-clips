"""
job_manager.py
---------------
Tiny in-memory job queue (no Redis/Celery needed for an MVP). Each job runs
the full pipeline in a background thread and reports progress that the
frontend polls.
"""

import os
import threading
import time
import traceback
import uuid

from services import transcript_service, video_service, caption_service, scorer

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")

PIPELINE_STEPS = [
    "fetching_video",
    "extracting_transcript",
    "detecting_moments",
    "generating_clips",
    "done",
]

_jobs = {}
_lock = threading.Lock()


def _new_job_record(youtube_url):
    return {
        "id": None,
        "youtube_url": youtube_url,
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
    job_id = uuid.uuid4().hex[:12]
    record = _new_job_record(youtube_url)
    record["id"] = job_id

    with _lock:
        _jobs[job_id] = record

    job_dir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

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


def _run_pipeline(job_id: str):
    job_dir = _job_dir(job_id)
    job = get_job(job_id)
    youtube_url = job["youtube_url"]

    try:
        # 1. Fetch video metadata + download source video
        _update(job_id, status="processing", step="fetching_video", progress=5)
        info = video_service.get_video_info(youtube_url)
        _update(job_id, video_title=info.get("title"), video_duration=info.get("duration"))

        source_path = video_service.download_source_video(youtube_url, job_dir)
        _update(job_id, progress=25)

        # 2. Extract transcript
        _update(job_id, step="extracting_transcript", progress=30)
        transcript, source = transcript_service.get_transcript(youtube_url, work_dir=job_dir)
        _update(job_id, transcript_source=source, progress=45)

        # 3. Detect viral moments
        _update(job_id, step="detecting_moments", progress=50)
        top_clips = scorer.select_top_clips(transcript, top_n=10)
        if not top_clips:
            raise RuntimeError("No clip-worthy moments were found in this video's transcript.")
        _update(job_id, progress=55, clip_progress={"current": 0, "total": len(top_clips)})

        # 4. Generate clips (cut + captions + thumbnail)
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

    except Exception as exc:
        _update(job_id, status="error", error=str(exc))
        traceback.print_exc()
