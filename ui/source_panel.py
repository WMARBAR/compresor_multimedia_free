"""Tarjeta SOURCE MEDIA: vista previa y metadatos del archivo (o del lote) cargado."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
from PIL import Image

from services.common import format_bytes
from services.metadata_service import MediaMetadata

from . import theme
from .widgets import Card, KeyValueList, blank_image, label

PREVIEW_MAX_SIZE = (300, 170)


class SourcePanel(Card):
    def __init__(self, master) -> None:
        super().__init__(master, "SOURCE MEDIA")
        self._preview_image: ctk.CTkImage | None = None
        self._preview = ctk.CTkLabel(
            self, text="SIN ARCHIVO", font=theme.mono_font(11), text_color=theme.FAINT,
            fg_color=theme.CARD_ALT, corner_radius=6, width=PREVIEW_MAX_SIZE[0], height=PREVIEW_MAX_SIZE[1],
        )
        self.add(self._preview, pady=(0, 6), sticky="w")
        self._caption = label(self, "", size=10, color=theme.MUTED, mono=True, wraplength=320)
        self.add(self._caption, pady=(0, 4))
        self._rows = KeyValueList(self, value_limit=38)
        self.add(self._rows, pady=(0, 4))
        self._names = ctk.CTkTextbox(
            self, height=70, wrap="none", font=theme.mono_font(11), fg_color=theme.CARD_ALT,
            text_color=theme.TEXT, border_width=1, border_color=theme.BORDER, state="disabled",
        )
        self.add(self._names, pady=(0, theme.INNER))
        self._names.grid_remove()
        self.show_empty()

    def _set_preview(self, preview) -> None:
        image = Image.fromarray(preview) if not isinstance(preview, Image.Image) else preview
        image = image.copy()
        image.thumbnail(PREVIEW_MAX_SIZE, Image.LANCZOS)  # nunca se muestra la imagen completa en la interfaz
        self._preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
        self._preview.configure(image=self._preview_image, text="")

    def show_empty(self) -> None:
        self._preview_image = None
        self._preview.configure(image=blank_image(), text="SIN ARCHIVO")
        self._caption.grid_remove()
        self._rows.set_rows([("Estado", "Sin archivo")], theme.MUTED)
        self._names.grid_remove()

    def show_single(self, meta: MediaMetadata) -> None:
        self._set_preview(meta.preview)
        self._caption.grid_remove()
        self._rows.set_rows(meta.rows())
        self._names.grid_remove()

    def show_batch(self, paths: list[Path], total_bytes: int, preview_meta: MediaMetadata | None) -> None:
        if preview_meta is not None:
            self._set_preview(preview_meta.preview)
            self._caption.configure(text=f"Vista previa del lote: {preview_meta.path.name}")
            self._caption.grid()
        self._rows.set_rows([
            ("Selección", f"{len(paths)} IMÁGENES"),
            ("Tamaño total", format_bytes(total_bytes)),
        ])
        self._names.configure(state="normal")
        self._names.delete("1.0", "end")
        self._names.insert("end", "\n".join(path.name for path in paths))
        self._names.configure(state="disabled")
        self._names.grid()
