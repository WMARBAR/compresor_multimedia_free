"""Tema visual: negro + verde Matrix contenido (utilidad de FFmpeg / panel de desarrollo).

Todos los colores y fuentes viven aquí para que ninguna otra parte de la interfaz los repita.
La paleta es siempre oscura; el verde se reserva para acentos, bordes sutiles y estados correctos.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

# --- Colores ------------------------------------------------------------------------------
BG = "#0a0d0a"            # fondo de la ventana
CARD = "#101510"          # tarjetas
CARD_ALT = "#0c100c"      # zonas hundidas dentro de una tarjeta (log, filas)
BORDER = "#1d3b25"        # borde verde sutil
BORDER_STRONG = "#2a5c3a"

ACCENT = "#1fe068"        # verde Matrix (sin neón excesivo)
ACCENT_HOVER = "#3cf082"
ACCENT_DIM = "#123d22"    # relleno de hover de botones con borde
ON_ACCENT = "#04120a"     # texto sobre el verde

TEXT = "#e6ede6"
MUTED = "#93a395"         # contraste ≥ 7:1 sobre BG
FAINT = "#6f7f72"

WARN = "#f2c14e"
ERROR = "#ff6b6b"
OK = ACCENT

DISABLED_BG = "#161c16"
DISABLED_TEXT = "#657066"

# --- Fuentes ------------------------------------------------------------------------------
_WINDOWS = sys.platform.startswith("win")
UI_FAMILY = "Segoe UI" if _WINDOWS else "Helvetica"
MONO_FAMILY = "Consolas" if _WINDOWS else "Courier"


def ui_font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=UI_FAMILY, size=size, weight=weight)


def mono_font(size: int = 12, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FAMILY, size=size, weight=weight)


# --- Espaciado ----------------------------------------------------------------------------
PAD = 16        # margen exterior de la ventana
GAP = 12        # separación entre tarjetas
INNER = 14      # relleno dentro de una tarjeta
RADIUS = 8


def apply_theme() -> None:
    """Modo oscuro fijo; los widgets reciben sus colores explícitos desde este módulo."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")


# --- Estilos de widgets (diccionarios para ``**``) -----------------------------------------
PRIMARY_BUTTON = dict(
    fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
    text_color_disabled=DISABLED_TEXT, border_width=0, corner_radius=RADIUS,
)
SECONDARY_BUTTON = dict(
    fg_color="transparent", hover_color=ACCENT_DIM, text_color=TEXT, text_color_disabled=DISABLED_TEXT,
    border_width=1, border_color=BORDER_STRONG, corner_radius=RADIUS,
)
DANGER_BUTTON = dict(
    fg_color="transparent", hover_color="#3a1616", text_color=ERROR, text_color_disabled=DISABLED_TEXT,
    border_width=1, border_color="#6b2a2a", corner_radius=RADIUS,
)
ENTRY = dict(
    fg_color=CARD_ALT, border_color=BORDER_STRONG, border_width=1, text_color=TEXT,
    placeholder_text_color=FAINT, corner_radius=6,
)
OPTION_MENU = dict(
    fg_color=CARD_ALT, button_color=BORDER_STRONG, button_hover_color=ACCENT_DIM, text_color=TEXT,
    text_color_disabled=DISABLED_TEXT, dropdown_fg_color=CARD, dropdown_hover_color=ACCENT_DIM,
    dropdown_text_color=TEXT, corner_radius=6,
)
SEGMENTED = dict(
    fg_color=CARD_ALT, selected_color="#1a7a44", selected_hover_color="#22925a", unselected_color=CARD_ALT,
    unselected_hover_color=ACCENT_DIM, text_color=TEXT, text_color_disabled=DISABLED_TEXT, corner_radius=6,
)
