"""
transcription_service.py
-------------------------
Generates a timestamped transcript for an uploaded local video file using
AssemblyAI's free-tier API. The video is uploaded to AssemblyAI, they run
speech-to-text on their servers (no RAM cost on our end), and we get back
a timestamped transcript.

Free tier: 5 hours of audio per month, no credit card needed.
"""

import os
import time
import subprocess
import tempfile

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")


def _extract_audio(media_path: str) -> str:
    """
    Pulls just the audio track out as a 16kHz mono WAV file.
    Returns the path to the extracted audio file.
    """
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg", "-y",
        "-i", media_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        wav_path,
        "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return media_path

    return wav_path


def transcribe_video(media_path: str, language: str = "en"):
    """
    Uploads audio to AssemblyAI and returns a timestamped transcript:
        [{"start": float, "duration": float, "text": str}, ...]

    Raises RuntimeError if transcription fails or finds no speech.
    """
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError(
            "ASSEMBLYAI_API_KEY environment variable is not set. "
            "Get a free key at assemblyai.com and add it to Render's "
            "environment variables."
        )

    import assemblyai as aai
    aai.settings.api_key = ASSEMBLYAI_API_KEY

    audio_path = _extract_audio(media_path)
    extracted_temp = audio_path != media_path

    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_path)

        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(
                f"AssemblyAI transcription failed: {transcript.error}"
            )

        if not transcript.words:
            raise RuntimeError(
                "Could not detect any speech in this video. Try a clip "
                "with clearer spoken audio."
            )

        # Group individual words back into sentence-sized chunks so the
        # scorer has meaningful segments to work with (same shape as the
        # YouTube caption path: {"start", "duration", "text"}).
        entries = []
        chunk_words = []
        chunk_start = None

        for word in transcript.words:
            if chunk_start is None:
                chunk_start = word.start / 1000.0  # ms -> seconds

            chunk_words.append(word.text)
            chunk_end = word.end / 1000.0

            # Start a new chunk every ~10 words or at natural sentence ends
            if len(chunk_words) >= 10 or word.text.endswith((".", "!", "?")):
                entries.append({
                    "start": chunk_start,
                    "duration": max(chunk_end - chunk_start, 0.1),
                    "text": " ".join(chunk_words),
                })
                chunk_words = []
                chunk_start = None

        # flush any remaining words
        if chunk_words and chunk_start is not None:
            chunk_end = transcript.words[-1].end / 1000.0
            entries.append({
                "start": chunk_start,
                "duration": max(chunk_end - chunk_start, 0.1),
                "text": " ".join(chunk_words),
            })

        if not entries:
            raise RuntimeError(
                "Could not detect any speech in this video. Try a clip "
                "with clearer spoken audio."
            )

        return entries

    finally:
        if extracted_temp:
            try:
                os.remove(audio_path)
            except OSError:
                pass
