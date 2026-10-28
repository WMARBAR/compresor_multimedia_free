"""Historial de conversiones de la sesión actual (solo en memoria; se pierde al cerrar la app)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_ENTRIES = 30


@dataclass(frozen=True)
class HistoryEntry:
    name: str  # archivo de salida, o «24 imágenes» para un lote
    kind: str  # «video» o «imagen»
    before: int  # bytes originales
    after: int  # bytes finales
    location: Path  # archivo de salida o carpeta del lote
    ok: bool = True  # False si el resultado pesa más que el original
    finished_at: datetime | None = None


class SessionHistory:
    """Lista acotada, la más reciente primero."""

    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._limit = limit
        self._entries: list[HistoryEntry] = []

    def add(self, entry: HistoryEntry) -> None:
        self._entries.insert(0, entry)
        del self._entries[self._limit:]

    def remove_location(self, location: Path) -> None:
        """Quita las entradas de un archivo que el usuario descartó."""
        self._entries = [item for item in self._entries if item.location != location]

    @property
    def entries(self) -> list[HistoryEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
