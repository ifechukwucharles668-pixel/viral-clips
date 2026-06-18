"""
app.py
------
Flask API for the Viral Clips MVP.

Endpoints:
  POST /api/jobs                                  -> create a job, returns {job_id}
  GET  /api/jobs/<job_id>                          -> full job status + clips
  GET  /api/jobs/<job_id>/clips/<clip_id>/video     -> stream/download the clip mp4
  GET  /api/jobs/<job_id>/clips/<clip_id>/srt       -> download the clip .srt
  GET  /api/jobs/<job_id>/clips/<clip_id>/thumbnail -> clip thumbnail jpg
  GET  /api/health                                  -> simple healthcheck
"""

import os

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

from jobs import create_job, get_job, get_job_dir

app = Flask(__name__)
CORS(app)  # allow the separately-hosted frontend (e.g. Vercel) to call this API

MAX_URL_LENGTH = 500


def _public_job_view(job: dict) -> dict:
    """Strip internal/server-only fields before sending to the client."""
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job["step"],
        "progress": job["progress"],
        "clip_progress": job["clip_progress"],
        "error": job["error"],
        "video_title": job["video_title"],
        "video_duration": job["video_duration"],
        "transcript_source": job["transcript_source"],
        "clips": [
            {k: v for k, v in clip.items()} for clip in job["clips"]
        ],
    }


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/jobs")
def create_job_endpoint():
    data = request.get_json(silent=True) or {}
    youtube_url = (data.get("youtube_url") or "").strip()

    if not youtube_url:
        return jsonify({"error": "youtube_url is required"}), 400
    if len(youtube_url) > MAX_URL_LENGTH:
        return jsonify({"error": "youtube_url is too long"}), 400
    if "youtube.com" not in youtube_url and "youtu.be" not in youtube_url:
        return jsonify({"error": "Please provide a valid YouTube URL"}), 400

    job_id = create_job(youtube_url)
    return jsonify({"job_id": job_id}), 201


@app.get("/api/jobs/<job_id>")
def get_job_endpoint(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(_public_job_view(job))


def _resolve_clip_file(job_id, clip_id, filename_key):
    job = get_job(job_id)
    if not job:
        abort(404, description="job not found")

    clip = next((c for c in job["clips"] if c["id"] == clip_id), None)
    if not clip:
        abort(404, description="clip not found")

    path = os.path.join(get_job_dir(job_id), clip[filename_key])
    if not os.path.exists(path):
        abort(404, description="file not ready yet")

    return path


@app.get("/api/jobs/<job_id>/clips/<clip_id>/video")
def get_clip_video(job_id, clip_id):
    path = _resolve_clip_file(job_id, clip_id, "video_file")
    download = request.args.get("download") == "1"
    return send_file(path, mimetype="video/mp4", conditional=True,
                      as_attachment=download, download_name=f"{clip_id}.mp4")


@app.get("/api/jobs/<job_id>/clips/<clip_id>/srt")
def get_clip_srt(job_id, clip_id):
    path = _resolve_clip_file(job_id, clip_id, "srt_file")
    return send_file(path, mimetype="text/plain", as_attachment=True,
                      download_name=f"{clip_id}.srt")


@app.get("/api/jobs/<job_id>/clips/<clip_id>/thumbnail")
def get_clip_thumbnail(job_id, clip_id):
    path = _resolve_clip_file(job_id, clip_id, "thumbnail_file")
    return send_file(path, mimetype="image/jpeg", conditional=True)


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": str(e.description) if hasattr(e, "description") else "not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
