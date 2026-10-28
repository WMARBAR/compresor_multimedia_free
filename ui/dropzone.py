"""Zona de carga: botones VIDEO / IMAGEN y área para soltar o seleccionar archivos."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from . import theme
from .widgets import Card, label, make_focusable


class DropZone(Card):
    """Muestra qué se acepta; todo el recuadro es clicable y los archivos se pueden soltar sobre la ventana."""

    def __init__(
        self,
        master,
        video_formats: str,
        image_formats: str,
        on_pick_video: Callable[[], None],
        on_pick_image: Callable[[], None],
        on_pick_any: Callable[[], None],
        drag_supported: bool,
    ) -> None:
        super().__init__(master)
        self._on_pick_any = on_pick_any
        self.configure(border_color=theme.BORDER_STRONG, cursor="hand2")
        self.grid_columnconfigure((0, 1), weight=1, uniform="pick")

        big = theme.mono_font(16, "bold")
        self.video_button = ctk.CTkButton(
            self, text="[ VIDEO ]", height=44, font=big, command=on_pick_video,
            **{**theme.SECONDARY_BUTTON, "border_color": theme.ACCENT},
        )
        make_focusable(self.video_button).grid(row=0, column=0, padx=(theme.INNER, 6), pady=(theme.INNER, 8), sticky="ew")
        self.image_button = ctk.CTkButton(
            self, text="[ IMAGEN ]", height=44, font=big, command=on_pick_image,
            **{**theme.SECONDARY_BUTTON, "border_color": theme.ACCENT},
        )
        make_focusable(self.image_button).grid(row=0, column=1, padx=(6, theme.INNER), pady=(theme.INNER, 8), sticky="ew")

        headline = "ARRASTRA TU ARCHIVO AQUÍ" if drag_supported else "SELECCIONA TU ARCHIVO"
        self._texts = [
            label(self, headline, size=14, weight="bold", mono=True, anchor="center", justify="center"),
            label(self, "o haz clic para seleccionar", size=12, color=theme.MUTED, anchor="center", justify="center"),
            label(
                self,
                f"VIDEO (uno a la vez): {video_formats}\nIMAGEN (una o varias): {image_formats}",
                size=10, color=theme.MUTED, mono=True, anchor="center", justify="center",
            ),
        ]
        for row, widget in enumerate(self._texts, start=1):
            widget.grid(row=row, column=0, columnspan=2, padx=theme.INNER, pady=(0, 2 if row < 3 else theme.INNER))
        for widget in (self, *self._texts):
            widget.bind("<Button-1>", self._clicked)

    def _clicked(self, _event=None) -> None:
        if self.video_button.cget("state") != "disabled":
            self._on_pick_any()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.video_button.configure(state=state)
        self.image_button.configure(state=state)
        self.configure(cursor="hand2" if enabled else "arrow")
