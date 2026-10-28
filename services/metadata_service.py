"""Inspección de archivos al cargarlos: solo se informa lo que realmente se detecta.

Cada campo ausente es ``None`` y la interfaz lo omite; nunca se inventan valores. Pensado para
ejecutarse en un hilo (decodifica el primer frame / la imagen para la vista previa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .common import ConversionError, format_bytes, format_duration
from .image_processor import IMAGE_INPUT_EXTENSIONS, has_alpha, image_size, load_image, to_rgb_preview
from .video_processor import VIDEO_INPUT_EXTENSIONS, pretty_codec, probe_video

logger = logging.getLogger(__name__)

IMAGE_KIND = "imagen"
VIDEO_KIND = "video"

_FRAME_COUNT_TOLERANCE = 0.05  # el conteo de frames solo se muestra si concuerda con la duración


@dataclass(frozen=True)
class MediaMetadata:
    """Metadatos de un archivo cargado y su vista previa (RGB uint8)."""

    kind: str
    path: Path
    file_size: int
    width: int
    height: int
    preview: np.ndarray = field(repr=False, compare=False)
    container: str | None = None  # «MP4», «PNG»...
    # Video
    fps: float | None = None
    duration_seconds: float | None = None
    frame_count: int | None = None  # solo si es fiable
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool | None = None
    # Imagen
    channels: int | None = None
    channel_layout: str | None = None
    bit_depth: int | None = None
    has_alpha: bool | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def rows(self) -> list[tuple[str, str]]:
        """Pares (campo, valor) para la tarjeta «Archivo de origen»; solo con datos detectados."""
        rows = [
            ("Archivo", self.path.name),
            ("Resolución", f"{self.width} × {self.height} px"),
            ("Tamaño", format_bytes(self.file_size)),
        ]
        if self.kind == VIDEO_KIND:
            if self.container:
                rows.append(("Contenedor", self.container))
            if self.video_codec:
                rows.append(("Códec video", self.video_codec))
            if self.fps:
                rows.append(("FPS", f"{self.fps:g}"))
            if self.duration_seconds is not None:
                rows.append(("Duración", format_duration(self.duration_seconds)))
            if self.frame_count:
                rows.append(("Frames", f"{self.frame_count}"))
            if self.has_audio is True:
                rows.append(("Audio", self.audio_codec or "presente"))
            elif self.has_audio is False:
                rows.append(("Audio", "NINGUNO"))
        else:
            if self.container:
                rows.append(("Formato", self.container))
            if self.channels:
                layout = f" ({self.channel_layout})" if self.channel_layout else ""
                rows.append(("Canales", f"{self.channels}{layout}"))
            if self.bit_depth:
                rows.append(("Profundidad", f"{self.bit_depth} bits/canal"))
            if self.has_alpha is not None:
                rows.append(("Transparencia", "Sí (canal alfa)" if self.has_alpha else "No"))
        return rows


_CHANNEL_LAYOUTS = {1: "grises", 3: "RGB", 4: "RGBA"}
_PIL_FORMAT_NAMES = {"JPEG": "JPG", "MPO": "JPG", "TIFF": "TIFF", "WEBP": "WEBP", "PNG": "PNG", "BMP": "BMP"}


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise ConversionError("No se pudo leer el archivo. Verifica que exista y que tengas permisos.") from exc


def _image_format_name(path: Path) -> str | None:
    """Formato real según la cabecera del archivo (no la extensión); ``None`` si no se puede leer."""
    try:
        with Image.open(path) as handle:
            detected = handle.format
    except Exception:
        return None
    return _PIL_FORMAT_NAMES.get(detected or "", detected)


def inspect_image(path: Path) -> MediaMetadata:
    """Decodifica la imagen y devuelve sus metadatos (lanza ``ConversionError`` si no se puede leer)."""
    file_size = _file_size(path)
    image = load_image(path)
    width, height = image_size(image)
    channels = 1 if image.ndim == 2 else int(image.shape[2])
    return MediaMetadata(
        kind=IMAGE_KIND,
        path=path,
        file_size=file_size,
        width=width,
        height=height,
        preview=to_rgb_preview(image),
        container=_image_format_name(path),
        channels=channels,
        channel_layout=_CHANNEL_LAYOUTS.get(channels),
        bit_depth=16 if image.dtype == np.uint16 else 8,
        has_alpha=has_alpha(image),
    )


def inspect_video(path: Path) -> MediaMetadata:
    """Sondea el video (OpenCV + FFmpeg). Lanza ``ConversionError`` si no se puede abrir."""
    file_size = _file_size(path)
    info = probe_video(path)
    frame_count = info.frame_count
    if frame_count and info.container_duration:
        expected = info.container_duration * info.fps
        if abs(frame_count - expected) > _FRAME_COUNT_TOLERANCE * max(expected, 1.0):
            frame_count = None  # conteo poco fiable: es mejor no mostrarlo
    duration = info.container_duration if info.container_duration else info.duration_seconds
    return MediaMetadata(
        kind=VIDEO_KIND,
        path=path,
        file_size=file_size,
        width=info.width,
        height=info.height,
        preview=to_rgb_preview(info.first_frame),
        container=path.suffix.lstrip(".").upper() or None,
        fps=info.fps,
        duration_seconds=duration,
        frame_count=frame_count,
        video_codec=pretty_codec(info.video_codec),
        audio_codec=pretty_codec(info.audio_codec),
        has_audio=info.has_audio,
    )


def inspect_media(path: Path, kind: str) -> MediaMetadata:
    return inspect_video(path) if kind == VIDEO_KIND else inspect_image(path)


@dataclass(frozen=True)
class LoadPlan:
    """Qué se va a cargar a partir de una selección o de archivos soltados."""

    kind: str
    files: list[Path]
    warnings: list[str]


def plan_load(paths: list[Path]) -> LoadPlan:
    """Clasifica los archivos por extensión: varias imágenes o un solo video (nunca mezclados).

    Lanza ``ConversionError`` si nada es utilizable. Los archivos ignorados se informan en ``warnings``.
    """
    warnings: list[str] = []
    images: list[Path] = []
    videos: list[Path] = []
    for path in paths:
        suffix = path.suffix.lower()
        if not path.is_file():
            warnings.append(f"Se ignoró «{path.name}»: no es un archivo.")
        elif suffix in IMAGE_INPUT_EXTENSIONS:
            images.append(path)
        elif suffix in VIDEO_INPUT_EXTENSIONS:
            videos.append(path)
        else:
            warnings.append(f"Se ignoró «{path.name}»: formato no compatible.")
    if images and videos:
        raise ConversionError("Selecciona solo imágenes o un solo video, no ambos tipos a la vez.")
    if videos:
        if len(videos) > 1:
            warnings.append(f"Solo se procesa un video a la vez: se cargó «{videos[0].name}».")
        return LoadPlan(VIDEO_KIND, [videos[0]], warnings)
    if images:
        return LoadPlan(IMAGE_KIND, images, warnings)
    raise ConversionError(
        "Ningún archivo tiene un formato compatible. "
        f"Video: {', '.join(e.lstrip('.').upper() for e in VIDEO_INPUT_EXTENSIONS)}. "
        f"Imagen: {', '.join(e.lstrip('.').upper() for e in IMAGE_INPUT_EXTENSIONS)}."
    )
