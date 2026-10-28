"""Indicadores de compresión calculados a partir de tamaños reales de archivo.

No confundir la **razón de compresión** (original ÷ final, p. ej. ``2.88 : 1``) con el
**porcentaje de reducción** (espacio ahorrado, p. ej. ``65.3 %``): miden lo mismo con
escalas distintas. Si el archivo final pesa más que el original no se habla de «ahorro».
"""

from __future__ import annotations

from dataclasses import dataclass

from .common import format_bytes


@dataclass(frozen=True)
class CompressionStats:
    """Peso antes y después de una conversión (en bytes)."""

    before: int
    after: int

    @property
    def saved(self) -> int:
        """Bytes ahorrados (negativo si el resultado pesa más)."""
        return self.before - self.after

    @property
    def reduction_percent(self) -> float:
        """Espacio ahorrado respecto al original (negativo si creció)."""
        return self.saved / self.before * 100.0 if self.before > 0 else 0.0

    @property
    def ratio(self) -> float | None:
        """Razón original : final, o ``None`` si el archivo final está vacío."""
        return self.before / self.after if self.after > 0 and self.before > 0 else None

    @property
    def is_larger(self) -> bool:
        return self.after > self.before


def format_ratio(stats: CompressionStats) -> str:
    """``2.88 : 1`` (o «—» si no se puede calcular)."""
    ratio = stats.ratio
    return f"{ratio:.2f} : 1" if ratio is not None else "—"


def format_saved(stats: CompressionStats) -> str:
    """Espacio ahorrado como texto; «0 B» si no cambió y «+x» si el resultado creció."""
    if stats.saved == 0:
        return format_bytes(0)
    if stats.is_larger:
        return f"+{format_bytes(-stats.saved)}"
    return format_bytes(stats.saved)


def format_reduction(stats: CompressionStats) -> str:
    """``65.3%`` de reducción, o ``-8.4%`` si el resultado es más grande."""
    return f"{stats.reduction_percent:.1f}%"


def larger_warning(stats: CompressionStats) -> str | None:
    """Mensaje honesto cuando el resultado pesa más que el original (nunca se presenta como éxito de compresión)."""
    if not stats.is_larger:
        return None
    growth = (stats.after / stats.before - 1.0) * 100.0 if stats.before > 0 else 0.0
    return f"El archivo resultante es {growth:.1f}% más grande que el original."
