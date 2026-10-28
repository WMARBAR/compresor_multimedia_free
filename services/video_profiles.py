"""Perfiles de calidad de video: resolución final y calidad de compresión H.264 (CRF).

Son dos decisiones independientes: un video puede pesar mucho menos sin cambiar su
resolución (solo por una compresión H.264 más eficiente), y reducir la resolución no
garantiza menos artefactos. La resolución se calcula aquí; el reescalado lo hace FFmpeg
(Lanczos) y nunca se amplía un video.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .common import ConversionError, calculate_output_size

CRF_MIN = 17
CRF_MAX = 28
CRF_WARNING_ABOVE = 23
DEFAULT_PROFILE = "high"

# Etiquetas de ayuda para el CRF (las que no aparecen se muestran solo con el número).
CRF_GUIDE = {
    18: "Muy alta calidad",
    20: "Alta calidad",
    21: "Buena calidad",
    23: "Mayor compresión",
}
CRF_SEVERE_FROM = 27  # el rango termina en 28: desde aquí se advierte de pérdida considerable
CRF_HIGH_WARNING = "Con CRF superior a 23 pueden aumentar los artefactos visibles (bloques, pérdida de detalle)."
CRF_SEVERE_WARNING = "Esta configuración puede producir una pérdida considerable de calidad."
STRONG_REDUCTION_FROM = 50.0  # reducción lineal (%) desde la que se avisa
STRONG_REDUCTION_WARNING = "La resolución se reducirá significativamente."

# Presets de x264 ofrecidos en el modo avanzado (más lento = archivo más pequeño a igual CRF).
PRESETS = ("veryfast", "fast", "medium", "slow", "slower")
DEFAULT_PRESET = "medium"
MIN_DIMENSION = 64
MAX_DIMENSION = 16384
COMPRESSION_WARNING = "Puede reducir notablemente la nitidez del video."

WEB_LONG_SIDE = 1280
WEB_SHORT_SIDE = 720
HIGH_COMPRESSION_REDUCTION = 50.0


@dataclass(frozen=True)
class ProfileInfo:
    """Datos de un perfil para mostrarlo en la interfaz."""

    key: str
    label: str          # nombre completo en el selector
    short_label: str    # para el resumen final («Calidad: Alta (CRF 20)»)
    description: str
    warning: str | None = None


PROFILES: dict[str, ProfileInfo] = {
    "max": ProfileInfo(
        "max", "Máxima calidad", "Máxima",
        "Conserva la resolución original. CRF 18, preset slow: máxima nitidez, archivo más grande.",
    ),
    "high": ProfileInfo(
        "high", "Alta calidad", "Alta",
        "Conserva la resolución original. CRF 20: mucha calidad con un tamaño razonable (recomendado).",
    ),
    "web": ProfileInfo(
        "web", "Web optimizado", "Web optimizado",
        "Hasta 1280×720 (sin ampliar nunca), reescalado Lanczos. CRF 20.",
    ),
    "compression": ProfileInfo(
        "compression", "Compresión alta", "Compresión alta",
        "Reduce la resolución al 50 % por lado. CRF 23.", COMPRESSION_WARNING,
    ),
    "custom": ProfileInfo(
        "custom", "Personalizado", "Personalizado",
        "Tú eliges la reducción de resolución (0 % = conservar) y el CRF.",
    ),
}
PROFILE_BY_LABEL = {info.label: info for info in PROFILES.values()}


def _even(value: int) -> int:
    """Baja al par inmediato inferior (mínimo 2): H.264 yuv420p exige lados pares."""
    return max(2, value - value % 2)


@dataclass(frozen=True)
class VideoQuality:
    """Ajustes de codificación ya resueltos para una conversión."""

    key: str
    label: str
    short_label: str
    crf: int
    preset: str
    audio_bitrate: str
    reduction_percent: float | None = None   # reducción porcentual de cada lado
    fit_box: tuple[int, int] | None = None   # (lado largo máx., lado corto máx.)
    max_width: int | None = None             # límite de ancho (nunca amplía, conserva la relación de aspecto)
    max_height: int | None = None

    @property
    def quality_text(self) -> str:
        return f"{self.short_label} (CRF {self.crf})"

    def target_size(self, width: int, height: int) -> tuple[int, int]:
        """Resolución final (siempre par). Nunca es mayor que la original."""
        if width < 1 or height < 1:
            raise ConversionError("Las dimensiones originales no son válidas.")
        if self.reduction_percent:
            base = calculate_output_size(width, height, self.reduction_percent, even=True)
        else:
            base = _even(width), _even(height)
            if self.fit_box:
                long_limit, short_limit = self.fit_box
                scale = min(1.0, long_limit / max(width, height), short_limit / min(width, height))
                if scale < 1.0:
                    base = _even(int(round(width * scale))), _even(int(round(height * scale)))
        return self._apply_limits(base)

    def _apply_limits(self, size: tuple[int, int]) -> tuple[int, int]:
        """Aplica el ancho/alto máximos del modo avanzado: se reduce todo por igual, sin estirar ni ampliar."""
        width, height = size
        scale = min(
            1.0,
            self.max_width / width if self.max_width else 1.0,
            self.max_height / height if self.max_height else 1.0,
        )
        if scale < 1.0:
            return _even(int(round(width * scale))), _even(int(round(height * scale)))
        return size

    def needs_resize(self, width: int, height: int) -> bool:
        return self.target_size(width, height) != (width, height)


def parse_video_reduction(text: object) -> float:
    """Reducción de resolución para el modo personalizado: 0 (conservar) a 99 %."""
    cleaned = str(text).strip().removesuffix("%").strip()
    if not cleaned:
        raise ConversionError("Ingresa la reducción de resolución (0 para conservarla, hasta 99).")
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", cleaned):
        raise ConversionError("La reducción debe ser un número entre 0 y 99 (sin letras ni signos).")
    value = float(cleaned.replace(",", "."))
    if not 0 <= value <= 99:
        raise ConversionError("La reducción debe estar entre 0 y 99 %.")
    return value


def parse_crf(value: object) -> int:
    """CRF entero entre 17 y 28."""
    try:
        crf = int(str(value).strip().split()[0])
    except (ValueError, IndexError):
        raise ConversionError(f"El CRF debe ser un número entero entre {CRF_MIN} y {CRF_MAX}.") from None
    if not CRF_MIN <= crf <= CRF_MAX:
        raise ConversionError(f"El CRF debe estar entre {CRF_MIN} y {CRF_MAX}.")
    return crf


def parse_preset(value: object) -> str:
    preset = str(value).strip().lower()
    if preset not in PRESETS:
        raise ConversionError(f"El preset debe ser uno de: {', '.join(PRESETS)}.")
    return preset


def parse_dimension(value: object, label: str) -> int | None:
    """Ancho o alto máximo opcional del modo avanzado: vacío = sin límite."""
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise ConversionError(f"El {label} máximo debe ser un número entero de píxeles (o déjalo vacío).")
    number = int(cleaned)
    if not MIN_DIMENSION <= number <= MAX_DIMENSION:
        raise ConversionError(f"El {label} máximo debe estar entre {MIN_DIMENSION} y {MAX_DIMENSION} píxeles.")
    return number


def make_quality(
    key: str = DEFAULT_PROFILE,
    custom_reduction: object = 0,
    custom_crf: object = 20,
    custom_preset: object = DEFAULT_PRESET,
    max_width: object = "",
    max_height: object = "",
) -> VideoQuality:
    """Resuelve un perfil (los valores personalizados solo se usan con ``key == "custom"``)."""
    info = PROFILES.get(key)
    if info is None:
        raise ConversionError(f"Perfil de calidad desconocido: {key}.")
    common = dict(key=info.key, label=info.label, short_label=info.short_label)
    if key == "max":
        return VideoQuality(**common, crf=18, preset="slow", audio_bitrate="192k")
    if key == "high":
        return VideoQuality(**common, crf=20, preset="medium", audio_bitrate="160k")
    if key == "web":
        return VideoQuality(**common, crf=20, preset="medium", audio_bitrate="128k",
                            fit_box=(WEB_LONG_SIDE, WEB_SHORT_SIDE))
    if key == "compression":
        return VideoQuality(**common, crf=23, preset="medium", audio_bitrate="128k",
                            reduction_percent=HIGH_COMPRESSION_REDUCTION)
    return VideoQuality(**common, crf=parse_crf(custom_crf), preset=parse_preset(custom_preset),
                        audio_bitrate="128k",
                        reduction_percent=parse_video_reduction(custom_reduction) or None,
                        max_width=parse_dimension(max_width, "ancho"),
                        max_height=parse_dimension(max_height, "alto"))
