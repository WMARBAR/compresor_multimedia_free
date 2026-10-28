"""Tarjeta RECENT CONVERSIONS: historial en memoria de la sesión actual."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import customtkinter as ctk

from services.common import format_bytes
from services.history import HistoryEntry

from . import theme
from .widgets import Card, ToolTip, label, shorten_middle


class HistoryPanel(Card):
    def __init__(self, master, on_open: Callable[[Path], None]) -> None:
        super().__init__(master, "RECENT CONVERSIONS")
        self._on_open = on_open
        self._rows = ctk.CTkScrollableFrame(
            self, fg_color=theme.CARD_ALT, height=120, corner_radius=6,
            scrollbar_button_color=theme.BORDER_STRONG, scrollbar_button_hover_color=theme.ACCENT_DIM,
        )
        self._rows.grid_columnconfigure(1, weight=1)
        self.add(self._rows, pady=(0, theme.INNER))
        self._tooltips: list[ToolTip] = []
        self.set_entries([])

    def set_entries(self, entries: list[HistoryEntry]) -> None:
        for child in self._rows.winfo_children():
            child.destroy()
        self._tooltips.clear()
        if not entries:
            label(self._rows, "Sin conversiones en esta sesión.", size=12, color=theme.MUTED, mono=True).grid(
                row=0, column=0, columnspan=3, padx=6, pady=6, sticky="w")
            return
        for index, entry in enumerate(entries):
            symbol = "✓" if entry.ok else "⚠"
            color = theme.ACCENT if entry.ok else theme.WARN
            label(self._rows, symbol, size=12, color=color, mono=True).grid(row=index, column=0, padx=(6, 6), pady=2)
            name = label(self._rows, shorten_middle(entry.name, 26), size=12, mono=True)
            name.grid(row=index, column=1, sticky="w", pady=2)
            sizes = label(self._rows, f"{format_bytes(entry.before)} → {format_bytes(entry.after)}", size=12,
                          color=color, mono=True)
            sizes.grid(row=index, column=2, padx=(12, 6), sticky="e", pady=2)
            for widget in (name, sizes):
                widget.bind("<Button-1>", lambda _e, location=entry.location: self._on_open(location))
                widget.configure(cursor="hand2")
            self._tooltips.append(ToolTip(name, f"{entry.name}\nClic para abrir la ubicación"))
