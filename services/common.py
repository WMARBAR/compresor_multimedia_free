"""Utilidades compartidas por los procesadores de imagen y video.

Contiene las rutas del proyecto, la validación del porcentaje de reducción,
el cálculo de la resolución de salida y la reserva de nombres de salida que
nunca sobrescriben archivos existentes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# La raíz se calcula desde la ubicación de este archivo, no desde el cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outs"
OUTPUT_SUFFIX = "_convertido"
BATCH_FOLDER_FORMAT = "%Y-%m-%d_%H-%M-%S"

MIN_PERCENT = 1
MAX_PERCENT = 99

_PERCENT_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?$")


class ConversionError(Exception):
    """Error esperado cuyo mensaje (en español) puede mostrarse al usuario."""


class ConversionCancelled(Exception):
    """El usuario canceló la conversión en curso."""


@dataclass
class ConversionResult:
    """Resultado de una conversión exitosa."""

    output_path: Path
    output_size: tuple[int, int]
    warnings: list[str] = field(default_factory=list)
    audio_preserved: bool | None = None  # None: no aplica (imágenes)
    frames_processed: int | None = None
    # Solo videos (verificados sobre el archivo final):
    source_size: tuple[int, int] | None = None
    format_label: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_source_audio: bool | None = None
    quality_label: str | None = None  # p. ej. «Alta (CRF 20)»
    source_bytes: int | None = None


@dataclass
class BatchResult:
    """Resultado de convertir varias imágenes con los mismos ajustes."""

    output_dir: Path  # carpeta del lote (o ``outs/`` si solo había una imagen)
    total: int
    succeeded: list[ConversionResult] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)  # (origen, motivo)
    cancelled: bool = False
    used_batch_folder: bool = False

    @property
    def processed(self) -> int:
        return len(self.succeeded) + len(self.failed)


def ensure_output_dir(output_dir: Path = OUTPUT_DIR) -> Path:
    """Crea la carpeta de salida si no existe y la devuelve."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConversionError(
            f"No se pudo crear la carpeta de salida ({output_dir}). "
            "Revisa los permisos de la carpeta del proyecto."
        ) from exc
    return output_dir


def parse_percentage(text: object) -> float:
    """Valida el porcentaje de reducción escrito por el usuario.

    Acepta un número positivo (con punto o coma decimal y un ``%`` opcional)
    entre 1 y 99. Rechaza vacío, letras, negativos, NaN/inf, 0 y 100 o más.
    """
    cleaned = str(text).strip().removesuffix("%").strip()
    if not cleaned:
        raise ConversionError("Ingresa un porcentaje de reducción entre 1 y 99.")
    if not _PERCENT_PATTERN.match(cleaned):
        raise ConversionError("El porcentaje debe ser un número entre 1 y 99 (sin letras ni signos).")
    value = float(cleaned.replace(",", "."))
    if not MIN_PERCENT <= value <= MAX_PERCENT:
        raise ConversionError(f"El porcentaje debe estar entre {MIN_PERCENT} y {MAX_PERCENT}.")
    return value


def calculate_output_size(
    width: int, height: int, reduction_percent: float, even: bool = False
) -> tuple[int, int]:
    """Calcula la resolución de salida para una reducción dada.

    Reducir un ``X %`` significa que cada lado conserva ``(100 - X) %`` de su
    longitud: 1920 × 1080 reducido un 50 % queda en 960 × 540. Ambos lados se
    escalan con el mismo factor, por lo que la relación de aspecto se conserva
    (salvo el redondeo a píxeles enteros).

    Con ``even=True`` cada lado se baja al par inmediato inferior (mínimo 2),
    porque muchos códecs de video no admiten dimensiones impares.
    """
    if width < 1 or height < 1:
        raise ConversionError("Las dimensiones originales no son válidas.")
    factor = 1.0 - reduction_percent / 100.0
    new_width = max(1, int(round(width * factor)))
    new_height = max(1, int(round(height * factor)))
    if even:
        new_width = max(2, new_width - new_width % 2)
        new_height = max(2, new_height - new_height % 2)
    return new_width, new_height


def reserve_output_path(source: Path, extension: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Reserva ``<nombre>_convertido<ext>`` en ``outs/`` sin pisar nada.

    Si el nombre ya existe prueba ``_1``, ``_2``, ... El archivo se crea vacío
    de forma atómica (modo ``x``) para que dos conversiones no obtengan el
    mismo nombre. Quien reserva debe borrarlo si la conversión falla.
    """
    directory = ensure_output_dir(output_dir)
    extension = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    base_name = f"{source.stem}{OUTPUT_SUFFIX}"
    index = 0
    while True:
        name = f"{base_name}{extension}" if index == 0 else f"{base_name}_{index}{extension}"
        candidate = directory / name
        try:
            with open(candidate, "xb"):
                pass
            return candidate
        except FileExistsError:
            index += 1
        except OSError as exc:
            raise ConversionError(
                f"No se pudo crear el archivo de salida en {directory}. "
                "Revisa los permisos o el espacio disponible."
            ) from exc


def create_batch_folder(output_dir: Path = OUTPUT_DIR, now: datetime | None = None) -> Path:
    """Crea ``outs/YYYY-MM-DD_HH-MM-SS/`` (hora local) para un lote de imágenes.

    Si la carpeta ya existe prueba ``_1``, ``_2``, ... La creación es atómica
    (``exist_ok=False``), así que nunca se reutiliza ni se mezcla con una
    carpeta anterior. El formato no contiene ``:`` para ser válido en Windows.
    """
    directory = ensure_output_dir(output_dir)
    base_name = (now or datetime.now()).strftime(BATCH_FOLDER_FORMAT)
    index = 0
    while True:
        candidate = directory / (base_name if index == 0 else f"{base_name}_{index}")
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            index += 1
        except OSError as exc:
            raise ConversionError(
                f"No se pudo crear la carpeta del lote en {directory}. Revisa los permisos o el espacio disponible."
            ) from exc


def discard_reserved(path: Path) -> None:
    """Borra un archivo de salida reservado por nosotros tras un fallo."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def display_path(path: Path) -> str:
    """Ruta legible: relativa a la raíz del proyecto cuando es posible (``outs/x.mp4``)."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def format_bytes(size: int) -> str:
    """Tamaño legible como el del Explorador de Windows: ``14.8 MB``."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(seconds: float | None) -> str:
    """Formatea segundos como ``MM:SS`` o ``HH:MM:SS``."""
    if seconds is None or seconds < 0:
        return "desconocida"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
