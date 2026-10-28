"""Lectura, reducción y conversión de video con OpenCV (+ FFmpeg para el audio)."""

from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .common import (
    OUTPUT_DIR,
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    calculate_output_size,
    discard_reserved,
    parse_percentage,
    reserve_output_path,
)
from .ffmpeg_tools import FFMPEG_MISSING_WARNING, find_ffmpeg, has_audio_stream, mux_original_audio

logger = logging.getLogger(__name__)

VIDEO_INPUT_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg", ".wmv")

# Códecs FourCC candidatos por contenedor, en orden de preferencia. Solo se
# ofrece un formato si OpenCV logra escribir y releer un video de prueba.
VIDEO_CODEC_CANDIDATES: dict[str, tuple[str, ...]] = {
    ".mp4": ("mp4v", "avc1"),
    ".avi": ("XVID", "MJPG"),
    ".mov": ("mp4v", "avc1"),
}

_MAX_PLAUSIBLE_FPS = 1000.0
_PROGRESS_INTERVAL_SECONDS = 0.1

ProgressCallback = Callable[[int, "int | None"], None]


@dataclass(frozen=True)
class VideoInfo:
    """Metadatos del video y su primer frame (para la vista previa)."""

    width: int
    height: int
    fps: float
    frame_count: int | None  # None si el contenedor no lo informa de forma fiable
    duration_seconds: float | None
    first_frame: np.ndarray


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
    return VideoInfo(width, height, fps, frame_count, duration, frame)


@lru_cache(maxsize=None)
def _working_fourcc(extension: str) -> str | None:
    """Primer FourCC con el que OpenCV puede escribir *y releer* un video de prueba."""
    for fourcc in VIDEO_CODEC_CANDIDATES.get(extension, ()):
        folder = Path(tempfile.mkdtemp(prefix="conv_codec_"))
        test_file = folder / f"test{extension}"
        writer = None
        try:
            writer = cv2.VideoWriter(str(test_file), cv2.VideoWriter_fourcc(*fourcc), 25.0, (64, 64))
            if not writer.isOpened():
                continue
            blank = np.zeros((64, 64, 3), dtype=np.uint8)
            for _ in range(5):
                writer.write(blank)
            writer.release()
            writer = None
            if test_file.exists() and test_file.stat().st_size > 0:
                capture = cv2.VideoCapture(str(test_file))
                try:
                    if capture.isOpened() and capture.read()[0]:
                        return fourcc
                finally:
                    capture.release()
        except cv2.error:
            logger.exception("Prueba de códec %s falló", fourcc)
        finally:
            if writer is not None:
                writer.release()
            shutil.rmtree(folder, ignore_errors=True)
    return None


def available_video_formats() -> dict[str, str]:
    """Formatos de video que esta instalación puede producir: ``{"MP4": ".mp4", ...}``."""
    return {ext.lstrip(".").upper(): ext for ext in VIDEO_CODEC_CANDIDATES if _working_fourcc(ext)}


def video_audio_status() -> tuple[bool, str | None]:
    """``(audio_disponible, aviso)``: si FFmpeg permite conservar el audio original."""
    if find_ffmpeg() is None:
        return False, FFMPEG_MISSING_WARNING
    return True, None


def convert_video(
    source: Path,
    reduction_percent: float,
    format_label: str,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> ConversionResult:
    """Reduce un video y lo guarda en ``outs/`` como ``<nombre>_convertido.<ext>``.

    Flujo: OpenCV lee cada frame, lo reescala con ``INTER_AREA`` y lo escribe
    en un video temporal (mismos FPS que el original). Si FFmpeg está
    disponible y el original tiene audio, se mezcla ese audio en el resultado
    final; si no, el resultado queda sin audio y se informa en ``warnings``.
    Los archivos temporales se eliminan siempre; el original no se toca.

    ``progress_callback(procesados, total)`` se invoca desde el hilo que
    ejecuta esta función (``total`` es ``None`` si se desconoce).
    """
    reduction_percent = parse_percentage(reduction_percent)
    formats = available_video_formats()
    extension = formats.get(format_label.upper())
    if extension is None:
        raise ConversionError(f"El formato de salida «{format_label}» no está disponible en esta instalación.")
    fourcc_name = _working_fourcc(extension)

    info = probe_video(source)
    target_width, target_height = calculate_output_size(info.width, info.height, reduction_percent, even=True)

    warnings: list[str] = []
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        warnings.append(FFMPEG_MISSING_WARNING)

    output_path = reserve_output_path(source, extension, output_dir)
    temp_dir = Path(tempfile.mkdtemp(prefix="conv_video_"))
    completed = False
    try:
        resized_video = temp_dir / f"resized{extension}"
        frames_processed = _resize_frames(
            source, resized_video, fourcc_name, info, (target_width, target_height),
            progress_callback, cancel_event,
        )
        if info.frame_count and frames_processed < info.frame_count * 0.95:
            warnings.append(
                f"Solo se pudieron leer {frames_processed} de {info.frame_count} frames; "
                "el video original podría tener partes dañadas."
            )

        final_video, audio_preserved = resized_video, False
        if ffmpeg is not None:
            if has_audio_stream(ffmpeg, source):
                muxed_video = temp_dir / f"muxed{extension}"
                if mux_original_audio(ffmpeg, resized_video, source, muxed_video, cancel_event):
                    final_video, audio_preserved = muxed_video, True
                else:
                    warnings.append("No se pudo copiar el audio original; el video se generó sin audio.")
            else:
                warnings.append("El video original no tiene pista de audio.")

        if cancel_event is not None and cancel_event.is_set():
            raise ConversionCancelled()
        try:
            shutil.copyfile(final_video, output_path)
        except OSError as exc:
            logger.exception("No se pudo escribir %s", output_path)
            raise ConversionError(
                "No se pudo guardar el video de salida. Revisa permisos y espacio en disco."
            ) from exc
        completed = True
        return ConversionResult(
            output_path, (target_width, target_height), warnings, audio_preserved, frames_processed
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not completed:
            discard_reserved(output_path)


def _resize_frames(
    source: Path,
    destination: Path,
    fourcc_name: str,
    info: VideoInfo,
    target_size: tuple[int, int],
    progress_callback: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> int:
    """Lee ``source`` frame a frame y escribe la versión reducida en ``destination``."""
    capture = _open_capture(source)
    writer: cv2.VideoWriter | None = None
    processed = 0
    last_report = 0.0
    try:
        writer = cv2.VideoWriter(
            str(destination), cv2.VideoWriter_fourcc(*fourcc_name), info.fps, target_size
        )
        if not writer.isOpened():
            raise ConversionError(
                f"No se pudo crear el video de salida: el códec «{fourcc_name}» no está disponible "
                "en esta instalación de OpenCV."
            )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ConversionCancelled()
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            writer.write(cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA))
            processed += 1
            now = time.monotonic()
            if progress_callback is not None and now - last_report >= _PROGRESS_INTERVAL_SECONDS:
                last_report = now
                progress_callback(processed, info.frame_count)
    except cv2.error as exc:
        logger.exception("Fallo de OpenCV procesando %s", source)
        raise ConversionError("Ocurrió un error al procesar los frames del video.") from exc
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if processed == 0:
        raise ConversionError("No se pudo leer ningún frame del video.")
    if not destination.exists() or destination.stat().st_size == 0:
        raise ConversionError("No se pudo escribir el video temporal. Revisa el espacio en disco.")
    if progress_callback is not None:
        progress_callback(processed, info.frame_count or processed)
    return processed
