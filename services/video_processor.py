"""Conversión de video: FFmpeg hace todo el trabajo; OpenCV solo lee metadatos y valida el resultado.

Flujo (una sola codificación, sin generaciones intermedias):

    video original ──► FFmpeg ──► [reescalado Lanczos opcional] ──► H.264 (CRF) + AAC, yuv420p, faststart
         (video + audio)                                                  │
                                                         validación del archivo final ──► outs/

OpenCV se usa únicamente para abrir el video (vista previa, resolución, FPS, frames) y para
comprobar que el archivo generado se abre y tiene las dimensiones esperadas. No se procesan
frames con OpenCV ni se genera ningún video intermedio (tampoco con ``cv2.VideoWriter``).
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .common import (
    OUTPUT_DIR,
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    discard_reserved,
    reserve_output_path,
)
from .ffmpeg_service import (
    ContainerProfile,
    FFmpegInfo,
    FFmpegTranscoder,
    FFmpegUnavailableError,
    MediaProbe,
    available_profiles,
    build_transcode_command,
    get_ffmpeg,
    probe_media,
)
from .video_profiles import VideoQuality, make_quality

logger = logging.getLogger(__name__)

VIDEO_INPUT_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg", ".wmv")

_MAX_PLAUSIBLE_FPS = 1000.0

STAGE_PREPARING = "preparing"
STAGE_ENCODING = "encoding"
STAGE_VALIDATING = "validating"

# progress_callback(etapa, frames_procesados, total_de_frames | None)
ProgressCallback = Callable[[str, int, "int | None"], None]


@dataclass(frozen=True)
class VideoInfo:
    """Metadatos del video y su primer frame (para la vista previa)."""

    width: int
    height: int
    fps: float
    frame_count: int | None  # None si el contenedor no lo informa de forma fiable
    duration_seconds: float | None  # frames / fps
    first_frame: np.ndarray
    has_audio: bool | None  # None si no se pudo comprobar (FFmpeg no disponible)
    container_duration: float | None = None  # duración que informa el contenedor (video + audio)


def _windows_short_path(path: Path) -> str | None:
    """Ruta corta 8.3 (solo ASCII) para builds de OpenCV que fallan con tildes/ñ."""
    if os.name != "nt":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else None


def _open_capture(path: Path) -> cv2.VideoCapture:
    """Abre el video o lanza ``ConversionError`` con un mensaje amable."""
    if not path.is_file():
        raise ConversionError("El archivo no existe o no es accesible.")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        short_path = _windows_short_path(path)
        capture = cv2.VideoCapture(short_path) if short_path else capture
    if not capture.isOpened():
        capture.release()
        raise ConversionError(
            "No se pudo abrir el video. El archivo puede estar dañado, no ser un video "
            "o usar un códec que esta instalación de OpenCV no reconoce."
        )
    return capture


def probe_video(path: Path) -> VideoInfo:
    """Valida que OpenCV pueda abrir y decodificar el video y devuelve sus metadatos."""
    capture = _open_capture(path)
    try:
        ok, frame = capture.read()
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        reported_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    except cv2.error as exc:
        logger.exception("Fallo al leer %s", path)
        raise ConversionError("El video está dañado y no se pudo leer.") from exc
    finally:
        capture.release()

    if not ok or frame is None:
        raise ConversionError("El video se abrió, pero no se pudo decodificar ningún frame. Puede estar dañado.")
    if not math.isfinite(fps) or not 0 < fps <= _MAX_PLAUSIBLE_FPS:
        raise ConversionError(
            "No se pudo determinar los FPS del video (el archivo informa un valor inválido). "
            "Para no alterar la velocidad de reproducción, la conversión no se realizará."
        )

    frame_count = reported_frames if reported_frames > 0 else None
    duration = frame_count / fps if frame_count else None
    height, width = frame.shape[:2]
    try:
        media = probe_media(path)
        has_audio: bool | None = media.has_audio
        container_duration = media.duration_seconds
    except FFmpegUnavailableError:
        has_audio, container_duration = None, None
    return VideoInfo(width, height, fps, frame_count, duration, frame, has_audio, container_duration)


def available_video_formats() -> dict[str, str]:
    """Formatos de video que se pueden producir: ``{"MP4": ".mp4", ...}`` (vacío sin FFmpeg)."""
    return {label: profile.extension for label, profile in available_profiles().items()}


def convert_video(
    source: Path,
    format_label: str,
    quality: VideoQuality | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> ConversionResult:
    """Convierte un video y lo guarda en ``outs/`` como ``<nombre>_convertido.<ext>``.

    ``quality`` define la resolución final y el CRF de H.264 (por defecto «Alta calidad»:
    resolución original, CRF 20). FFmpeg genera el archivo final en una sola pasada: reescalado
    Lanczos solo si hay reducción, H.264 (libx264), ``yuv420p``, AAC si el original tiene audio
    y ``faststart``. Si FFmpeg no está disponible se lanza ``FFmpegUnavailableError`` (nunca se
    genera un video sin audio por omisión). El archivo se valida antes de entregarse; si algo
    falla no queda salida parcial.

    ``progress_callback(etapa, procesados, total)`` se invoca desde el hilo que ejecuta esta
    función (``total`` es ``None`` si el contenedor no informa el número de frames).
    """
    quality = quality or make_quality()
    ffmpeg = get_ffmpeg()
    profile = available_profiles().get(format_label.upper())
    if profile is None:
        raise ConversionError(f"El formato de salida «{format_label}» no está disponible.")

    def notify(stage: str, done: int = 0, total: int | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, done, total)

    notify(STAGE_PREPARING)
    info = probe_video(source)
    expect_audio = bool(info.has_audio)
    target_size = quality.target_size(info.width, info.height)
    resize_to = target_size if target_size != (info.width, info.height) else None
    if cancel_event is not None and cancel_event.is_set():
        raise ConversionCancelled()

    output_path = reserve_output_path(source, profile.extension, output_dir)
    temp_dir = Path(tempfile.mkdtemp(prefix="conv_video_"))
    completed = False
    try:
        temp_output = temp_dir / f"final{profile.extension}"
        command = build_transcode_command(ffmpeg, profile, quality, source, temp_output, resize_to, expect_audio)
        total_frames = info.frame_count

        def on_progress(frames: int, seconds: float | None) -> None:
            if total_frames is None and seconds is not None and info.container_duration:
                notify(STAGE_ENCODING, int(seconds * info.fps), int(info.container_duration * info.fps))
            else:
                notify(STAGE_ENCODING, frames, total_frames)

        notify(STAGE_ENCODING, 0, total_frames)
        with FFmpegTranscoder(command, temp_dir / "ffmpeg.log") as transcoder:
            transcoder.run(on_progress, cancel_event)
        notify(STAGE_ENCODING, total_frames or transcoder.frames, total_frames or transcoder.frames)

        notify(STAGE_VALIDATING)
        probe, output_frames = _verify_output(temp_output, ffmpeg, profile, info, target_size, expect_audio)

        if cancel_event is not None and cancel_event.is_set():
            raise ConversionCancelled()
        try:
            shutil.copyfile(temp_output, output_path)
        except OSError as exc:
            logger.exception("No se pudo escribir %s", output_path)
            raise ConversionError(
                "No se pudo guardar el video de salida. Revisa permisos y espacio en disco."
            ) from exc
        if output_path.stat().st_size != temp_output.stat().st_size:
            raise ConversionError("El archivo guardado quedó incompleto. Revisa el espacio en disco.")

        completed = True
        return ConversionResult(
            output_path,
            target_size,
            [],
            audio_preserved=expect_audio,
            frames_processed=output_frames,
            source_size=(info.width, info.height),
            format_label=profile.label,
            video_codec=_pretty_codec(probe.video_codec),
            audio_codec=_pretty_codec(probe.audio_codec) if expect_audio else None,
            has_source_audio=expect_audio,
            quality_label=quality.quality_text,
            source_bytes=source.stat().st_size,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not completed:
            discard_reserved(output_path)


def _pretty_codec(codec: str | None) -> str | None:
    if codec is None:
        return None
    return "H.264" if codec == "h264" else codec.upper()


def _verify_output(
    path: Path,
    ffmpeg: FFmpegInfo,
    profile: ContainerProfile,
    info: VideoInfo,
    expected_size: tuple[int, int],
    expect_audio: bool,
) -> tuple[MediaProbe, int]:
    """Comprueba el archivo final (lanza ``ConversionError`` si algo no cumple). Devuelve sondeo y frames."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ConversionError("FFmpeg no generó el archivo final.")

    problems: list[str] = []
    frames = 0
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened() or not capture.read()[0]:
            problems.append("no se puede abrir con OpenCV")
        else:
            size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            if size != expected_size:
                problems.append(f"resolución {size[0]}×{size[1]} en lugar de {expected_size[0]}×{expected_size[1]}")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if not math.isfinite(fps) or fps <= 0 or abs(fps - info.fps) > max(0.05 * info.fps, 0.5):
                problems.append(f"FPS {fps:g} en lugar de {info.fps:g}")
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if info.frame_count and frames and abs(frames - info.frame_count) > max(3, 0.02 * info.frame_count):
                problems.append(f"{frames} frames en lugar de {info.frame_count}")
    finally:
        capture.release()

    probe = probe_media(path, ffmpeg)
    if probe.video_codec != profile.expected_video_codec:
        problems.append(f"códec de video {probe.video_codec} en lugar de {profile.expected_video_codec}")
    if probe.pixel_format != profile.pixel_format:
        problems.append(f"formato de píxel {probe.pixel_format} en lugar de {profile.pixel_format}")
    if expect_audio and probe.audio_codec != profile.expected_audio_codec:
        problems.append(f"el audio del original no quedó en el archivo (códec {probe.audio_codec})")
    if not expect_audio and probe.audio_codec is not None:
        problems.append("el archivo tiene audio aunque el original no lo tenía")

    expected_duration = info.container_duration or info.duration_seconds
    if probe.duration_seconds is None:
        problems.append("no se pudo leer la duración")
    elif expected_duration and abs(probe.duration_seconds - expected_duration) > max(0.5, 0.03 * expected_duration):
        problems.append(f"duración {probe.duration_seconds:.2f} s en lugar de ~{expected_duration:.2f} s")

    if problems:
        logger.error("Validación del archivo final falló: %s", "; ".join(problems))
        raise ConversionError(
            "El video generado no superó la validación final y no se guardó. Detalle: "
            + "; ".join(problems) + "."
        )
    return probe, frames
