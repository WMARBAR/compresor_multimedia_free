"""Widgets reutilizables del tema: tarjetas, filas de datos, KPI, tooltips y registro tipo terminal."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from . import theme


def blank_image() -> ctk.CTkImage:
    """Imagen de 1×1 para «vaciar» un CTkLabel: ``image=None`` deja una referencia colgante a la imagen anterior.

    No se guarda en una variable global: pertenece al intérprete Tk de la ventana que la usa.
    """
    pixel = Image.new("RGB", (1, 1), theme.CARD_ALT)
    return ctk.CTkImage(light_image=pixel, dark_image=pixel, size=(1, 1))


def shorten_middle(text: str, limit: int) -> str:
    """Acorta con «…» en el medio conservando la extensión (``foto_muy_la…final.png``)."""
    if len(text) <= limit:
        return text
    suffix = Path(text).suffix if len(Path(text).suffix) <= 8 else ""
    stem = text[: len(text) - len(suffix)]
    keep = max(limit - len(suffix) - 1, 4)
    head = (keep + 1) // 2
    tail = keep - head
    return f"{stem[:head]}…{stem[len(stem) - tail:] if tail else ''}{suffix}"


class ToolTip:
    """Globo de texto al pasar el cursor (sirve también para ver nombres de archivo completos)."""

    DELAY_MS = 450

    def __init__(self, widget: tk.Misc, text: str = "") -> None:
        self._widget = widget
        self.text = text
        self._job: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if self.text:
            self._job = self._widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self._widget.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def _show(self) -> None:
        self._job = None
        if self._window is not None or not self.text:
            return
        try:
            x, y = self._widget.winfo_pointerx() + 14, self._widget.winfo_pointery() + 16
            window = tk.Toplevel(self._widget)
            window.wm_overrideredirect(True)
            window.wm_geometry(f"+{x}+{y}")
            window.configure(bg=theme.BORDER_STRONG)
            tk.Label(
                window, text=self.text, justify="left", bg=theme.CARD, fg=theme.TEXT,
                font=(theme.UI_FAMILY, 9), padx=8, pady=4, wraplength=420,
            ).pack(padx=1, pady=1)
            self._window = window
        except tk.TclError:
            self._window = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class Card(ctk.CTkFrame):
    """Tarjeta oscura con borde verde sutil y título opcional en mayúsculas."""

    def __init__(self, master, title: str | None = None, **kwargs) -> None:
        super().__init__(
            master, fg_color=theme.CARD, border_color=theme.BORDER, border_width=1,
            corner_radius=theme.RADIUS, **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)
        self.title_label: ctk.CTkLabel | None = None
        self._next_row = 0
        if title:
            self.title_label = ctk.CTkLabel(
                self, text=title, font=theme.mono_font(12, "bold"), text_color=theme.ACCENT, anchor="w"
            )
            self.title_label.grid(row=0, column=0, padx=theme.INNER, pady=(theme.INNER - 2, 6), sticky="ew")
            self._next_row = 1

    def add(self, widget, pady: tuple[int, int] | int = 4, padx: int | None = None, **grid) -> int:
        """Apila ``widget`` en la siguiente fila de la tarjeta y devuelve el número de fila."""
        row = self._next_row
        widget.grid(row=row, column=0, padx=theme.INNER if padx is None else padx, pady=pady,
                    sticky=grid.pop("sticky", "ew"), **grid)
        self._next_row += 1
        return row


def label(master, text: str = "", *, size: int = 13, weight: str = "normal", color: str = theme.TEXT,
          mono: bool = False, wraplength: int = 0, justify: str = "left", anchor: str = "w",
          height: int | None = None) -> ctk.CTkLabel:
    font = theme.mono_font(size, weight) if mono else theme.ui_font(size, weight)
    extra = {} if height is None else {"height": height}
    return ctk.CTkLabel(master, text=text, font=font, text_color=color, wraplength=wraplength,
                        justify=justify, anchor=anchor, **extra)


class KeyValueList(ctk.CTkFrame):
    """Lista «campo  valor» con valores en monoespaciado; los valores largos se acortan con tooltip."""

    LABEL_WIDTH = 104

    def __init__(self, master, value_limit: int = 40) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self._value_limit = value_limit
        self._tooltips: list[ToolTip] = []

    def set_rows(self, rows: list[tuple[str, str]], value_color: str = theme.TEXT) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._tooltips.clear()
        for index, (name, value) in enumerate(rows):
            key = label(self, name.upper(), size=10, color=theme.MUTED, mono=True, height=20)
            key.grid(row=index, column=0, sticky="w", pady=1, padx=(0, 10))
            shown = shorten_middle(value, self._value_limit)
            val = label(self, shown, size=12, color=value_color, mono=True, height=20)
            val.grid(row=index, column=1, sticky="w", pady=1)
            if shown != value:
                self._tooltips.append(ToolTip(val, value))


class Kpi(ctk.CTkFrame):
    """Cifra grande con título pequeño (ANTES, DESPUÉS, AHORRADO...)."""

    def __init__(self, master, title: str) -> None:
        super().__init__(master, fg_color=theme.CARD_ALT, border_color=theme.BORDER, border_width=1,
                         corner_radius=6)
        self.grid_columnconfigure(0, weight=1)
        label(self, title, size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, padx=10, pady=(8, 0), sticky="w")
        self._value = label(self, "—", size=17, weight="bold", mono=True)
        self._value.grid(row=1, column=0, padx=10, pady=(0, 2), sticky="w")
        self._note = label(self, "", size=10, color=theme.MUTED, mono=True)
        self._note.grid(row=2, column=0, padx=10, pady=(0, 8), sticky="w")

    def set(self, value: str, note: str = "", color: str = theme.TEXT) -> None:
        self._value.configure(text=value, text_color=color)
        self._note.configure(text=note)


class LogView(ctk.CTkTextbox):
    """Registro tipo terminal: verde = normal, amarillo = aviso, rojo = error (siempre con prefijo)."""

    PREFIX = {"info": "> ", "warn": "! ", "error": "✗ "}
    COLORS = {"info": theme.ACCENT, "warn": theme.WARN, "error": theme.ERROR, "muted": theme.MUTED}
    MAX_LINES = 400

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master, fg_color=theme.CARD_ALT, border_color=theme.BORDER, border_width=1, text_color=theme.ACCENT,
            font=theme.mono_font(12), wrap="word", state="disabled", corner_radius=6,
            scrollbar_button_color=theme.BORDER_STRONG, scrollbar_button_hover_color=theme.ACCENT_DIM, **kwargs,
        )
        for level, color in self.COLORS.items():
            self.tag_config(level, foreground=color)

    def write(self, message: str, level: str = "info") -> None:
        """Añade una línea (o varias; solo la primera lleva prefijo) y baja hasta el final."""
        self.configure(state="normal")
        lines = message.splitlines() or [""]
        prefix = self.PREFIX.get(level, "> ")
        text = "\n".join([prefix + lines[0], *("  " + line for line in lines[1:])]) + "\n"
        self.insert("end", text, level)
        total = int(self.index("end-1c").split(".")[0])
        if total > self.MAX_LINES:
            self.delete("1.0", f"{total - self.MAX_LINES}.0")
        self.see("end")
        self.configure(state="disabled")

    def lines(self) -> list[str]:
        return self.get("1.0", "end").splitlines()


def make_focusable(button: ctk.CTkButton) -> ctk.CTkButton:
    """Permite llegar al botón con Tab, activarlo con Espacio/Enter y ver el foco (CustomTkinter no lo hace solo)."""
    try:
        canvas = button._canvas  # el Canvas interno es lo que recibe el foco
        canvas.configure(takefocus=1, highlightthickness=0)

        def invoke(_event=None):
            if button.cget("state") != "disabled":
                button.invoke()
            return "break"

        def focus_in(_event=None):
            button.configure(border_width=2, border_color=theme.TEXT)

        def focus_out(_event=None):
            width, color = button._focus_border
            button.configure(border_width=width, border_color=color)

        button._focus_border = (button.cget("border_width"), button.cget("border_color"))
        canvas.bind("<space>", invoke)
        canvas.bind("<Return>", invoke)
        canvas.bind("<FocusIn>", focus_in, add="+")
        canvas.bind("<FocusOut>", focus_out, add="+")
        canvas.bind("<Button-1>", lambda _e: canvas.focus_set(), add="+")
    except Exception:  # detalle interno de CustomTkinter: si cambia, el botón sigue funcionando con el ratón
        pass
    return button


def primary_button(master, text: str, command, **kwargs) -> ctk.CTkButton:
    kwargs.setdefault("font", theme.ui_font(13, "bold"))
    return make_focusable(ctk.CTkButton(master, text=text, command=command, **{**theme.PRIMARY_BUTTON, **kwargs}))


def set_primary_enabled(button: ctk.CTkButton, enabled: bool) -> None:
    """CustomTkinter no cambia el relleno al deshabilitar: se atenúa a mano para que se note."""
    width, color = (0, theme.ACCENT) if enabled else (1, theme.BORDER)
    button.configure(
        state="normal" if enabled else "disabled",
        fg_color=theme.ACCENT if enabled else theme.DISABLED_BG, border_width=width, border_color=color,
    )
    button._focus_border = (width, color)  # al perder el foco se restaura este borde
    try:
        button._canvas.configure(takefocus=1 if enabled else 0)  # un botón deshabilitado no recibe el foco
    except Exception:
        pass


def secondary_button(master, text: str, command, **kwargs) -> ctk.CTkButton:
    kwargs.setdefault("font", theme.ui_font(12, "bold"))
    return make_focusable(ctk.CTkButton(master, text=text, command=command, **{**theme.SECONDARY_BUTTON, **kwargs}))


def danger_button(master, text: str, command, **kwargs) -> ctk.CTkButton:
    kwargs.setdefault("font", theme.ui_font(12, "bold"))
    return make_focusable(ctk.CTkButton(master, text=text, command=command, **{**theme.DANGER_BUTTON, **kwargs}))
