"""
transcription_service.py
-------------------------
Generates a timestamped transcript for an arbitrary local video/audio file
using faster-whisper — a free, locally-run speech-to-text model. No paid
APIs, no network calls beyond a one-time model download on first use.

This is the upload-a-video counterpart to transcript_service.py (which
handles the YouTube-caption path). Both produce the same shape of output:
a list of {"start": float, "duration": float, "text": str} entries, so the
rest of the pipeline (scorer, caption_service, video_service) doesn't need
to know or care which path produced the transcript.
"""

import os
import subprocess
import tempfile

# "tiny.en" is the smallest, fastest Whisper model — picked deliberately so
# this can run on small/free-tier hardware (e.g. Render's free plan) within
# a reasonable amount of time. It's English-only and less accurate than
# larger models, but is a solid trade-off for an MVP. Override via env var
# if you later move to a bigger instance and want better accuracy.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "tiny.en")

_model = None  # lazy-loaded, shared across requests in this process


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        # int8 compute type keeps memory/CPU usage low — important on
        # small free-tier instances. CPU-only since free tiers have no GPU.
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def _extract_audio(media_path: str) -> str:
    """
    Pulls just the audio track out as a 16kHz mono WAV file — exactly the
    format Whisper wants internally anyway. Feeding it this directly (rather
    than the original video file) means it never has to decode any video
    frames or resample audio, which is a real time-save on slow/free-tier
    CPUs. Returns the path to the extracted audio file; caller is
    responsible for deleting it afterward.
    """
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg", "-y",
        "-i", media_path,
        "-vn",                 # no video
        "-ac", "1",            # mono
        "-ar", "16000",        # 16kHz, Whisper's native sample rate
        "-c:a", "pcm_s16le",
        wav_path,
        "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Not fatal — fall back to transcribing the original file directly.
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return media_path

    return wav_path


def _run_transcription(model, media_path, language, vad_filter):
    segments, _info = model.transcribe(
        media_path,
        language=language,
        vad_filter=vad_filter,
        beam_size=1,  # fastest setting; good enough for an MVP
    )

    entries = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        start = float(seg.start)
        end = float(seg.end)
        entries.append({
            "start": start,
            "duration": max(end - start, 0.1),
            "text": text,
        })
    return entries


def transcribe_video(media_path: str, language: str = "en"):
    """
    Runs local speech-to-text on a video/audio file and returns a transcript
    in the same shape used everywhere else in this app:
        [{"start": float, "duration": float, "text": str}, ...]

    Raises RuntimeError if no speech could be detected/transcribed at all.
    """
    model = _get_model()

    audio_path = _extract_audio(media_path)
    extracted_a_temp_file = audio_path != media_path

    try:
        # vad_filter skips silent stretches (faster), but on quieter or more
        # compressed phone-mic audio it can sometimes mistake real speech
        # for silence. If the first pass finds nothing, retry once without
        # it before giving up.
        entries = _run_transcription(model, audio_path, language, vad_filter=True)
        if not entries:
            entries = _run_transcription(model, audio_path, language, vad_filter=False)
    finally:
        if extracted_a_temp_file:
            try:
                os.remove(audio_path)
            except OSError:
                pass

    if not entries:
        raise RuntimeError(
            "Could not detect any speech in this video. Try a clip with "
            "clearer spoken audio, recorded with the phone close to "
            "whoever is talking."
        )

    return entries
