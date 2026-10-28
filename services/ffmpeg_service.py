"""Servicio central de FFmpeg: descubrimiento, validación, codificación H.264/AAC y sondeo.

FFmpeg se obtiene, en este orden, de:

1. el binario administrado por el paquete ``imageio-ffmpeg`` (se instala con pip), y
2. el ``ffmpeg`` del sistema (``shutil.which``).

Si ninguno funciona se lanza ``FFmpegUnavailableError`` (no hay conversión de video silenciosa).
Todos los procesos se lanzan con listas de argumentos, sin intérprete de comandos.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .common import ConversionCancelled, ConversionError
from .video_profiles import VideoQuality

logger = logging.getLogger(__name__)

FFMPEG_UNAVAILABLE_MESSAGE = (
    "FFmpeg no está disponible. La conversión de video requiere FFmpeg para generar un archivo "
    "compatible y preservar el audio."
)
FFMPEG_READY_MESSAGE = "FFmpeg disponible · Audio preservado · H.264"
FFMPEG_READY_NO_AUDIO_MESSAGE = "FFmpeg disponible · El video original no contiene audio · H.264"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_REQUIRED_ENCODERS = ("libx264", "aac")


class FFmpegUnavailableError(ConversionError):
    """No se encontró un FFmpeg utilizable (con libx264 y AAC)."""


@dataclass(frozen=True)
class FFmpegInfo:
    path: Path
    version: str
    origin: str  # «imageio-ffmpeg» o «sistema»
    encoders: frozenset[str] = field(repr=False)


@dataclass(frozen=True)
class ContainerProfile:
    """Cómo se codifica cada contenedor de salida. Todos llevan H.264 + AAC."""

    label: str
    extension: str
    description: str
    video_encoder: str = "libx264"
    audio_encoder: str = "aac"
    expected_video_codec: str = "h264"
    expected_audio_codec: str = "aac"
    pixel_format: str = "yuv420p"
    faststart: bool = True


VIDEO_PROFILES: dict[str, ContainerProfile] = {
    "MP4": ContainerProfile(
        "MP4", ".mp4", "MP4 — Recomendado para web y compatibilidad (H.264 · AAC · yuv420p · faststart)"
    ),
    "MOV": ContainerProfile(
        "MOV", ".mov", "MOV — H.264 + AAC en contenedor QuickTime (para web usa MP4)"
    ),
}
AVI_NOTE = "AVI no está disponible: no es un contenedor adecuado para H.264 + AAC ni para web."


@dataclass(frozen=True)
class MediaProbe:
    """Datos de un archivo obtenidos de la salida informativa de FFmpeg."""

    video_codec: str | None
    pixel_format: str | None
    audio_codec: str | None
    duration_seconds: float | None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


# ------------------------------------------------------------------ detection --
_cached_info: FFmpegInfo | None = None
_cache_lock = threading.Lock()


def reset_ffmpeg_cache() -> None:
    """Olvida el FFmpeg detectado (útil para pruebas)."""
    global _cached_info
    with _cache_lock:
        _cached_info = None


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def _candidate_paths() -> Iterator[tuple[str, Path]]:
    """Candidatos en orden de prioridad (perezoso: el PATH solo se consulta si hace falta)."""
    try:
        import imageio_ffmpeg

        yield "imageio-ffmpeg", Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # paquete ausente o binario no encontrado
        logger.info("FFmpeg administrado (imageio-ffmpeg) no disponible: %s", exc)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        yield "sistema", Path(system_ffmpeg)


def _inspect(origin: str, path: Path) -> FFmpegInfo | None:
    """Comprueba que el ejecutable responde y trae libx264 y AAC."""
    try:
        version_run = _run([str(path), "-version"], timeout=20)
        encoders_run = _run([str(path), "-hide_banner", "-encoders"], timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("FFmpeg %s (%s) no se pudo ejecutar: %s", origin, path, exc)
        return None
    if version_run.returncode != 0 or encoders_run.returncode != 0:
        return None
    first_line = (version_run.stdout.splitlines() or ["ffmpeg"])[0]
    match = re.search(r"version\s+(\S+)", first_line)
    encoders = frozenset(
        parts[1] for parts in (line.split() for line in encoders_run.stdout.splitlines()) if len(parts) > 1
    )
    missing = [name for name in _REQUIRED_ENCODERS if name not in encoders]
    if missing:
        logger.warning("FFmpeg %s (%s) no incluye los codificadores: %s", origin, path, ", ".join(missing))
        return None
    return FFmpegInfo(path, match.group(1) if match else first_line, origin, encoders)


def get_ffmpeg() -> FFmpegInfo:
    """Devuelve el FFmpeg a usar (detección una sola vez; el resultado se reutiliza)."""
    global _cached_info
    with _cache_lock:
        if _cached_info is not None:
            return _cached_info
        for origin, path in _candidate_paths():
            info = _inspect(origin, path)
            if info is not None:
                logger.info("FFmpeg %s detectado (%s): %s", info.version, origin, path)
                _cached_info = info
                return info
    raise FFmpegUnavailableError(FFMPEG_UNAVAILABLE_MESSAGE)


def ffmpeg_status() -> tuple[FFmpegInfo | None, str | None]:
    """``(info, None)`` si FFmpeg está listo; ``(None, mensaje)`` si no."""
    try:
        return get_ffmpeg(), None
    except FFmpegUnavailableError as exc:
        return None, str(exc)


def available_profiles() -> dict[str, ContainerProfile]:
    """Formatos de video que se pueden producir con el FFmpeg detectado (vacío si no hay FFmpeg)."""
    info, _ = ffmpeg_status()
    if info is None:
        return {}
    return {
        label: profile
        for label, profile in VIDEO_PROFILES.items()
        if profile.video_encoder in info.encoders and profile.audio_encoder in info.encoders
    }


# -------------------------------------------------------------------- probing --
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_CODEC = re.compile(r"Video:\s*([\w]+)")
_PIXEL_FORMAT = re.compile(r",\s*((?:yuvj?|nv|rgb|bgr|gray|pal8|p0|ya)\w*)")
_AUDIO_CODEC = re.compile(r"Audio:\s*([\w]+)")


def probe_media(path: Path, ffmpeg: FFmpegInfo | None = None) -> MediaProbe:
    """Lee códecs y duración con ``ffmpeg -i`` (no hace falta ffprobe)."""
    ffmpeg = ffmpeg or get_ffmpeg()
    try:
        completed = _run([str(ffmpeg.path), "-hide_banner", "-i", str(path)], timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("No se pudo inspeccionar %s: %s", path, exc)
        return MediaProbe(None, None, None, None)

    video_codec = pixel_format = audio_codec = None
    for line in completed.stderr.splitlines():
        if "Stream #" not in line:
            continue
        if video_codec is None and "Video:" in line and "attached pic" not in line:
            video_codec = (_VIDEO_CODEC.search(line) or [None, None])[1]
            pixel_format = (_PIXEL_FORMAT.search(line.split("Video:", 1)[1]) or [None, None])[1]
        elif audio_codec is None and "Audio:" in line:
            audio_codec = (_AUDIO_CODEC.search(line) or [None, None])[1]

    duration = None
    if (match := _DURATION.search(completed.stderr)):
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return MediaProbe(video_codec, pixel_format, audio_codec, duration)


# ------------------------------------------------------------------- encoding --
def build_transcode_command(
    ffmpeg: FFmpegInfo,
    profile: ContainerProfile,
    quality: VideoQuality,
    source: Path,
    output: Path,
    resize_to: tuple[int, int] | None,
    with_audio: bool,
) -> list[str]:
    """Lista de argumentos: todo el trabajo (reescalado Lanczos, H.264, AAC, faststart) en un solo FFmpeg.

    Se lee el video original directamente (una única codificación, sin generaciones intermedias).
    ``resize_to`` es la resolución final cuando hay que reducir; ``None`` conserva la resolución
    sin pasar por ningún filtro de escala.
    """
    command = [
        str(ffmpeg.path), "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-i", str(source), "-map", "0:v:0",
    ]
    if with_audio:
        command += ["-map", "0:a:0"]
    if resize_to is not None:
        command += ["-vf", f"scale={resize_to[0]}:{resize_to[1]}:flags=lanczos"]
    command += [
        "-c:v", profile.video_encoder, "-profile:v", "high",
        "-crf", str(quality.crf), "-preset", quality.preset, "-pix_fmt", profile.pixel_format,
    ]
    command += ["-c:a", profile.audio_encoder, "-b:a", quality.audio_bitrate] if with_audio else ["-an"]
    if profile.faststart:
        command += ["-movflags", "+faststart"]
    command += ["-progress", "pipe:1", "-nostats", str(output)]
    return command


class FFmpegTranscoder:
    """Ejecuta un comando de FFmpeg informando el progreso y permitiendo cancelarlo.

    Se usa como administrador de contexto: al salir se termina cualquier proceso que siga vivo,
    así que no quedan ``ffmpeg.exe`` huérfanos.
    """

    def __init__(self, command: list[str], log_path: Path) -> None:
        self._command = command
        self._log_path = log_path
        self.frames = 0
        self.out_seconds: float | None = None
        self._log_handle = open(log_path, "wb")  # stderr a archivo: evita bloqueos por tubería llena
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._log_handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self._log_handle.close()
            logger.exception("No se pudo iniciar FFmpeg")
            raise ConversionError("No se pudo iniciar FFmpeg. Reinstala las dependencias con pip.") from exc
        self._reader = threading.Thread(target=self._read_progress, daemon=True)
        self._reader.start()

    @property
    def process(self) -> subprocess.Popen:
        return self._process

    def __enter__(self) -> "FFmpegTranscoder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.abort()

    def _read_progress(self) -> None:
        """Hilo lector de ``-progress pipe:1`` (líneas ``clave=valor``)."""
        try:
            for line in self._process.stdout:
                key, _, value = line.strip().partition("=")
                try:
                    if key == "frame":
                        self.frames = int(value)
                    elif key == "out_time_us":
                        self.out_seconds = int(value) / 1_000_000
                except ValueError:
                    continue  # «N/A» al inicio
        except (OSError, ValueError):
            pass

    def run(self, progress_callback: Callable[[int, float | None], None] | None = None,
            cancel_event: threading.Event | None = None) -> None:
        """Espera a que FFmpeg termine; lanza ``ConversionCancelled`` o ``ConversionError``."""
        while True:
            try:
                return_code = self._process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    self.abort()
                    raise ConversionCancelled()
                if progress_callback is not None:
                    progress_callback(self.frames, self.out_seconds)
        self._reader.join(timeout=5)
        self._log_handle.close()
        if return_code != 0:
            raise self._failure()

    def abort(self) -> None:
        """Termina FFmpeg si sigue activo y libera los recursos (idempotente)."""
        process = self._process
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.error("FFmpeg no terminó tras kill()")
        self._reader.join(timeout=5)
        for stream in (process.stdout, self._log_handle):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _failure(self) -> ConversionError:
        """Construye un error en español; el detalle técnico va solo al log."""
        try:
            detail = self._log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        logger.error("FFmpeg falló (código %s). Comando: %s\n%s", self._process.returncode, self._command, detail)
        lowered = detail.lower()
        if "unknown encoder" in lowered or "encoder libx264 not found" in lowered:
            return ConversionError("Este FFmpeg no incluye el códec H.264 (libx264) necesario para la conversión.")
        if "no space left" in lowered:
            return ConversionError("No hay espacio suficiente en disco para generar el video.")
        return ConversionError(
            "FFmpeg no pudo generar el video final. No se creó ningún archivo; "
            "el detalle técnico quedó en logs/app.log."
        )
