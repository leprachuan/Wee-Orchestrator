"""
Audio Transcriber Module
========================
Dual-backend speech-to-text: OpenAI Whisper API + local faster-whisper.
Used by Telegram, WebEx, and WebUI channels to transcribe voice messages.
"""

import os
import sys
import time
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# Audio extensions we support
AUDIO_EXTENSIONS = {
    ".ogg",
    ".opus",
    ".mp3",
    ".wav",
    ".m4a",
    ".webm",
    ".oga",
    ".flac",
    ".aac",
    ".wma",
}

# Backend options: "openai", "local", "auto"
# auto = try local first, fallback to OpenAI
DEFAULT_BACKEND = os.getenv("TRANSCRIPTION_BACKEND", "auto")
DEFAULT_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# OpenAI API key - check multiple env var names
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_IMAGE_API_KEY")

# Lazy-loaded local model
_local_model = None
_local_model_lock = None


def _get_lock():
    """Lazy-init threading lock."""
    global _local_model_lock
    if _local_model_lock is None:
        import threading

        _local_model_lock = threading.Lock()
    return _local_model_lock


def _load_local_model():
    """Load faster-whisper model (lazy, thread-safe)."""
    global _local_model
    with _get_lock():
        if _local_model is not None:
            return _local_model
        try:
            from faster_whisper import WhisperModel

            model_size = os.getenv("WHISPER_MODEL", "base")
            print(f"[AUDIO] Loading local whisper model: {model_size}", file=sys.stderr)
            _local_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            print(f"[AUDIO] Local whisper model loaded successfully", file=sys.stderr)
            return _local_model
        except ImportError:
            print(
                "[AUDIO] faster-whisper not installed, local transcription unavailable",
                file=sys.stderr,
            )
            return None
        except Exception as e:
            print(f"[AUDIO] Failed to load local whisper model: {e}", file=sys.stderr)
            return None


def is_audio_file(filename: str) -> bool:
    """Check if a filename has an audio extension."""
    return Path(filename).suffix.lower() in AUDIO_EXTENSIONS


def convert_to_wav(input_path: str) -> Optional[str]:
    """Convert audio file to WAV format using ffmpeg. Returns path to WAV file."""
    input_path = Path(input_path)
    if not input_path.exists():
        return None

    # If already WAV, return as-is
    if input_path.suffix.lower() == ".wav":
        return str(input_path)

    wav_path = input_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and wav_path.exists():
            return str(wav_path)
        else:
            print(
                f"[AUDIO] ffmpeg conversion failed: {result.stderr[:200]}",
                file=sys.stderr,
            )
            return None
    except Exception as e:
        print(f"[AUDIO] ffmpeg error: {e}", file=sys.stderr)
        return None


def transcribe_openai(audio_path: str) -> Optional[str]:
    """Transcribe audio using OpenAI Whisper API."""
    api_key = OPENAI_API_KEY
    if not api_key:
        print("[AUDIO] No OpenAI API key available for transcription", file=sys.stderr)
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1", file=audio_file, response_format="text"
            )

        text = response.strip() if isinstance(response, str) else str(response).strip()
        print(f"[AUDIO] OpenAI transcription: {len(text)} chars", file=sys.stderr)
        return text if text else None

    except Exception as e:
        print(f"[AUDIO] OpenAI transcription error: {e}", file=sys.stderr)
        return None


def transcribe_local(audio_path: str) -> Optional[str]:
    """Transcribe audio using local faster-whisper model."""
    model = _load_local_model()
    if model is None:
        return None

    try:
        # Convert to WAV for best compatibility
        wav_path = convert_to_wav(audio_path)
        if not wav_path:
            wav_path = audio_path  # Try original if conversion fails

        segments, info = model.transcribe(wav_path, beam_size=5)
        text = " ".join(segment.text.strip() for segment in segments)
        print(
            f"[AUDIO] Local transcription ({info.language}, {info.duration:.1f}s): {len(text)} chars",
            file=sys.stderr,
        )

        # Clean up WAV if we created it
        if wav_path != audio_path and Path(wav_path).exists():
            try:
                Path(wav_path).unlink()
            except Exception:
                pass

        return text.strip() if text.strip() else None

    except Exception as e:
        print(f"[AUDIO] Local transcription error: {e}", file=sys.stderr)
        return None


def transcribe(
    audio_path: str, backend: Optional[str] = None
) -> Tuple[Optional[str], str]:
    """
    Transcribe audio file to text.

    Args:
        audio_path: Path to audio file
        backend: "openai", "local", or "auto" (default from env)

    Returns:
        Tuple of (transcribed_text, backend_used)
        text is None if transcription failed
    """
    backend = backend or DEFAULT_BACKEND
    start = time.time()

    if not Path(audio_path).exists():
        print(f"[AUDIO] File not found: {audio_path}", file=sys.stderr)
        return None, "error"

    file_size = Path(audio_path).stat().st_size
    if file_size > 25 * 1024 * 1024:  # 25MB limit for Whisper API
        print(
            f"[AUDIO] File too large ({file_size / 1024 / 1024:.1f}MB), max 25MB",
            file=sys.stderr,
        )
        return None, "error"

    if file_size == 0:
        print(f"[AUDIO] Empty audio file", file=sys.stderr)
        return None, "error"

    text = None
    used = "none"

    if backend == "local":
        text = transcribe_local(audio_path)
        used = "local" if text else "failed"

    elif backend == "openai":
        text = transcribe_openai(audio_path)
        used = "openai" if text else "failed"

    elif backend == "auto":
        # Try local first (faster, no API cost), fallback to OpenAI
        text = transcribe_local(audio_path)
        if text:
            used = "local"
        else:
            text = transcribe_openai(audio_path)
            used = "openai" if text else "failed"

    elapsed = time.time() - start
    if text:
        print(
            f"[AUDIO] Transcribed via {used} in {elapsed:.1f}s: {text[:80]}...",
            file=sys.stderr,
        )
    else:
        print(
            f"[AUDIO] Transcription failed ({backend} backend, {elapsed:.1f}s)",
            file=sys.stderr,
        )

    return text, used


def get_status() -> dict:
    """Return status of transcription backends."""
    local_available = False
    try:
        import faster_whisper

        local_available = True
    except ImportError:
        pass

    openai_available = bool(OPENAI_API_KEY)

    return {
        "backend": DEFAULT_BACKEND,
        "openai_available": openai_available,
        "local_available": local_available,
        "whisper_model": DEFAULT_WHISPER_MODEL,
        "local_model_loaded": _local_model is not None,
        "supported_formats": sorted(AUDIO_EXTENSIONS),
    }
