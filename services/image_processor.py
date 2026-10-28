"""Conversión y reducción de imágenes con OpenCV."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .common import (
    OUTPUT_DIR,
    ConversionError,
    ConversionResult,
    calculate_output_size,
    discard_reserved,
    parse_percentage,
    reserve_output_path,
)

logger = logging.getLogger(__name__)

IMAGE_INPUT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")

_EXIF_ORIENTATION_TAG = 0x0112


@dataclass(frozen=True)
class ImageFormat:
    """Formato de imagen de salida y cómo codificarlo."""

    label: str
    extension: str
    encode_params: tuple[int, ...]
    supports_alpha: bool
    supports_16bit: bool


IMAGE_FORMATS: dict[str, ImageFormat] = {
    "JPG": ImageFormat("JPG", ".jpg", (cv2.IMWRITE_JPEG_QUALITY, 95), False, False),
    "PNG": ImageFormat("PNG", ".png", (cv2.IMWRITE_PNG_COMPRESSION, 6), True, True),
    "WEBP": ImageFormat("WEBP", ".webp", (cv2.IMWRITE_WEBP_QUALITY, 95), True, False),
    "BMP": ImageFormat("BMP", ".bmp", (), False, False),
    "TIFF": ImageFormat("TIFF", ".tiff", (), True, True),
}


@lru_cache(maxsize=1)
def available_image_formats() -> tuple[str, ...]:
    """Etiquetas de los formatos que esta instalación de OpenCV sí puede codificar."""
    sample = np.zeros((8, 8, 3), dtype=np.uint8)
    usable = []
    for label, fmt in IMAGE_FORMATS.items():
        try:
            ok, _ = cv2.imencode(fmt.extension, sample, list(fmt.encode_params))
        except cv2.error:
            ok = False
        if ok:
            usable.append(label)
    return tuple(usable)


def load_image(path: Path) -> np.ndarray:
    """Decodifica una imagen por su contenido (no por su extensión).

    Devuelve un arreglo 2D (grises), HxWx3 (BGR) o HxWx4 (BGRA), uint8 o
    uint16, con la orientación EXIF ya aplicada. Lanza ``ConversionError``
    con un mensaje amable si el archivo no se puede leer o decodificar.
    """
    try:
        raw = np.fromfile(str(path), dtype=np.uint8)  # soporta rutas con tildes/ñ en Windows
    except OSError as exc:
        raise ConversionError("No se pudo leer el archivo. Verifica que exista y que tengas permisos.") from exc
    if raw.size == 0:
        raise ConversionError("El archivo está vacío.")

    try:
        image = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    except cv2.error as exc:
        logger.exception("imdecode falló para %s", path)
        raise ConversionError("La imagen está dañada o su formato no es compatible.") from exc
    if image is None or image.size == 0:
        raise ConversionError("OpenCV no pudo leer el archivo como imagen. Puede estar dañado o no ser una imagen compatible.")

    return _apply_exif_orientation(_normalize_layout(image), path)


def _normalize_layout(image: np.ndarray) -> np.ndarray:
    """Deja la imagen en uint8/uint16 con 1, 3 o 4 canales."""
    if image.dtype not in (np.uint8, np.uint16):
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if image.ndim == 3 and image.shape[2] == 1:
        image = image[:, :, 0]
    elif image.ndim == 3 and image.shape[2] == 2:  # gris + alfa: nos quedamos con el gris
        image = image[:, :, 0]
    return np.ascontiguousarray(image)


def _apply_exif_orientation(image: np.ndarray, path: Path) -> np.ndarray:
    """Aplica la rotación EXIF (fotos de celular); OpenCV no lo hace con IMREAD_UNCHANGED."""
    try:
        with Image.open(path) as pil_image:
            orientation = pil_image.getexif().get(_EXIF_ORIENTATION_TAG, 1)
    except Exception:  # EXIF ausente o ilegible: seguimos sin rotar
        return image
    transforms = {
        2: lambda a: cv2.flip(a, 1),
        3: lambda a: cv2.rotate(a, cv2.ROTATE_180),
        4: lambda a: cv2.flip(a, 0),
        5: cv2.transpose,
        6: lambda a: cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE),
        7: lambda a: cv2.rotate(cv2.transpose(a), cv2.ROTATE_180),
        8: lambda a: cv2.rotate(a, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }
    transform = transforms.get(orientation)
    return np.ascontiguousarray(transform(image)) if transform else image


def image_size(image: np.ndarray) -> tuple[int, int]:
    """Devuelve ``(ancho, alto)``."""
    return int(image.shape[1]), int(image.shape[0])


def has_alpha(image: np.ndarray) -> bool:
    return image.ndim == 3 and image.shape[2] == 4


def _to_uint8(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    return np.rint(image.astype(np.float32) / 257.0).astype(np.uint8)


def flatten_alpha(image: np.ndarray) -> np.ndarray:
    """Compone una imagen BGRA sobre fondo blanco y devuelve BGR (mismo dtype)."""
    max_value = float(np.iinfo(image.dtype).max)
    color = image[:, :, :3].astype(np.float32)
    alpha = image[:, :, 3:4].astype(np.float32) / max_value
    flattened = color * alpha + max_value * (1.0 - alpha)  # fondo blanco
    return np.rint(flattened).astype(image.dtype)


def _prepare_for_format(image: np.ndarray, fmt: ImageFormat) -> np.ndarray:
    """Adapta canales y profundidad de bits a lo que el formato admite."""
    if has_alpha(image) and not fmt.supports_alpha:
        image = flatten_alpha(image)
    if image.dtype == np.uint16 and not fmt.supports_16bit:
        image = _to_uint8(image)
    if image.ndim == 2 and fmt.extension == ".webp":  # WebP exige 3 o 4 canales
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def to_rgb_preview(image: np.ndarray) -> np.ndarray:
    """Convierte cualquier imagen/frame a RGB uint8 para mostrarlo en pantalla."""
    if has_alpha(image):
        image = flatten_alpha(image)
    image = _to_uint8(image)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def convert_image(
    source: Path,
    reduction_percent: float,
    format_label: str,
    output_dir: Path = OUTPUT_DIR,
) -> ConversionResult:
    """Reduce una imagen y la guarda en ``outs/`` como ``<nombre>_convertido.<ext>``.

    El original nunca se modifica. Al pasar a JPG/BMP la transparencia se
    compone sobre fondo blanco. La imagen completa se reescala con
    ``cv2.INTER_AREA`` conservando la relación de aspecto (sin recortes).
    """
    reduction_percent = parse_percentage(reduction_percent)
    fmt = IMAGE_FORMATS.get(format_label.upper())
    if fmt is None or fmt.label not in available_image_formats():
        raise ConversionError(f"El formato de salida «{format_label}» no está disponible en esta instalación.")

    image = load_image(source)
    width, height = image_size(image)
    new_width, new_height = calculate_output_size(width, height, reduction_percent)
    try:
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
        prepared = _prepare_for_format(resized, fmt)
        ok, encoded = cv2.imencode(fmt.extension, prepared, list(fmt.encode_params))
    except cv2.error as exc:
        logger.exception("Fallo al redimensionar/codificar %s", source)
        raise ConversionError("No se pudo procesar la imagen. Puede estar dañada.") from exc
    if not ok:
        raise ConversionError(f"No se pudo codificar la imagen como {fmt.label}.")

    warnings: list[str] = []
    if has_alpha(image) and not fmt.supports_alpha:
        warnings.append(f"{fmt.label} no admite transparencia: se usó fondo blanco.")

    output_path = reserve_output_path(source, fmt.extension, output_dir)
    try:
        output_path.write_bytes(encoded.tobytes())
    except OSError as exc:
        discard_reserved(output_path)
        logger.exception("No se pudo escribir %s", output_path)
        raise ConversionError("No se pudo guardar el archivo de salida. Revisa permisos y espacio en disco.") from exc

    return ConversionResult(output_path, (new_width, new_height), warnings)
