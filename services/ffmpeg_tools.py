"""Detección y uso del ejecutable de FFmpeg del sistema (no se incluye con la app)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from .common import ConversionCancelled

logger = logging.getLogger(__name__)

FFMPEG_ENV_VAR = "FFMPEG_PATH"
FFMPEG_MISSING_WARNING = "FFmpeg no está disponible. El video convertido se generará sin audio."

# Códecs de audio a intentar (en orden) según el contenedor de salida.
AUDIO_CODECS: dict[str, tuple[str, ...]] = {
    ".mp4": ("aac", "libmp3lame"),
    ".mov": ("aac", "pcm_s16le"),
    ".avi": ("libmp3lame", "pcm_s16le"),
}
_LOSSY_AUDIO = {"aac", "libmp3lame"}
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*?: Audio:")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )


@lru_cache(maxsize=1)
def find_ffmpeg() -> Path | None:
    """Busca FFmpeg en la variable ``FFMPEG_PATH`` y luego en el ``PATH``.

    Devuelve la ruta solo si el ejecutable realmente responde a ``-version``.
    """
    candidates: list[str | None] = []
    configured = os.environ.get(FFMPEG_ENV_VAR)
    if configured:
        configured_path = Path(configured)
        candidates.append(shutil.which("ffmpeg", path=str(configured_path)) if configured_path.is_dir() else configured)
    candidates.append(shutil.which("ffmpeg"))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            process = _popen([candidate, "-version"])
            process.communicate(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode == 0:
            return Path(candidate)
    return None


def has_audio_stream(ffmpeg: Path, source: Path) -> bool:
    """Indica si el archivo tiene pista de audio (lee la lista de streams de FFmpeg)."""
    try:
        process = _popen([str(ffmpeg), "-hide_banner", "-i", str(source)])
        _, stderr = process.communicate(timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("No se pudo inspeccionar el audio de %s", source)
        return False
    return bool(_AUDIO_STREAM.search(stderr))


def mux_original_audio(
    ffmpeg: Path,
    video_only: Path,
    original: Path,
    destination: Path,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Une el video redimensionado con el audio del original en ``destination``.

    El video se copia sin recodificar; el audio se codifica con el primer
    códec compatible con el contenedor. Devuelve ``False`` si ningún intento
    funcionó (el llamador conserva entonces el video sin audio).
    """
    extension = destination.suffix.lower()
    for audio_codec in AUDIO_CODECS.get(extension, ("aac",)):
        command = [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_only), "-i", str(original),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", audio_codec,
        ]
        if audio_codec in _LOSSY_AUDIO:
            command += ["-b:a", "192k"]
        if extension in (".mp4", ".mov"):
            command += ["-movflags", "+faststart"]
        command += ["-shortest", str(destination)]

        process = _popen(command)
        while True:
            try:
                _, stderr = process.communicate(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    process.kill()
                    process.communicate()
                    raise ConversionCancelled()
        if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
            return True
        logger.warning("FFmpeg falló con audio %s: %s", audio_codec, stderr.strip())
    return False
