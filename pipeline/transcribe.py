"""ClipForge pipeline: extract 16k mono audio and transcribe with faster-whisper.

NOTE: faster-whisper + current PyAV crashes on file-path input
(`TypeError: open() got an unexpected keyword argument 'metadata_errors'`).
So we decode audio.wav ourselves with the stdlib `wave` module into a
float32 numpy array and hand the ndarray to `model.transcribe(...)`.
"""
import os
import sys
import json
import subprocess
import wave

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: F401


def extract_audio(job_dir: str) -> str:
    """ffmpeg source.mp4 -> audio.wav (16 kHz, mono, pcm_s16le). Returns its path."""
    src = os.path.join(job_dir, "source.mp4")
    out = os.path.join(job_dir, "audio.wav")
    if not os.path.exists(src):
        raise RuntimeError("source.mp4 not found in %s" % job_dir)
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000",
        "-acodec", "pcm_s16le", "-f", "wav",
        out,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(
            "ffmpeg audio extraction failed: %s" % proc.stderr.decode("utf-8", "replace")[-800:]
        )
    return out


def _load_wav_float32(wav_path: str):
    """Decode a 16-bit PCM wav into a mono float32 ndarray in [-1, 1]."""
    with wave.open(wav_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise RuntimeError("expected 16-bit PCM wav, got sample width %d bytes" % sampwidth)
    audio = np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        # Downmix to mono by averaging interleaved channels.
        audio = audio.reshape(-1, n_channels).mean(axis=1)
    return audio


def transcribe(job_dir: str) -> str:
    """Transcribe audio.wav -> <job_dir>/transcript.json. Returns its path."""
    wav_path = os.path.join(job_dir, "audio.wav")
    if not os.path.exists(wav_path):
        # Be defensive: extract if missing.
        wav_path = extract_audio(job_dir)

    from faster_whisper import WhisperModel

    audio = _load_wav_float32(wav_path)

    model = WhisperModel("small", device="cpu", compute_type="int8")

    def _run(vad):
        segs, inf = model.transcribe(
            audio,
            word_timestamps=True,
            vad_filter=vad,
            beam_size=1,
        )
        collected = []
        for seg in segs:
            words = []
            for w in (seg.words or []):
                words.append({
                    "w": w.word,
                    "s": round(float(w.start), 3) if w.start is not None else None,
                    "e": round(float(w.end), 3) if w.end is not None else None,
                })
            collected.append({
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": seg.text,
                "words": words,
            })
        return collected, inf

    # VAD trims long silences for clean speech, but Silero VAD drops sung/music
    # vocals entirely. If it yields nothing, retry without VAD so music-heavy
    # or heavily-scored sources still transcribe.
    out_segments, info = _run(True)
    if not out_segments:
        out_segments, info = _run(False)

    duration = None
    try:
        duration = float(info.duration)
    except Exception:
        duration = round(len(audio) / 16000.0, 3)

    language = getattr(info, "language", None) or "en"

    transcript = {
        "language": language,
        "duration": duration,
        "segments": out_segments,
    }

    out_path = os.path.join(job_dir, "transcript.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False)
    return out_path
