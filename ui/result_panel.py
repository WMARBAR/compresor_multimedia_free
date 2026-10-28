"""Tarjeta de resultados: video (ficha técnica), imagen (antes/después), lote (resumen) y errores.

Todas las cifras se calculan con los tamaños reales de los archivos (``CompressionStats``).
Cuando el resultado pesa más que el original se dice abiertamente; nunca se presenta como ahorro.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image

from services.common import (
    BatchResult,
    ConversionResult,
    display_path,
    format_bytes,
    format_clock,
    format_elapsed,
)
from services.compression_stats import (
    CompressionStats,
    format_ratio,
    format_reduction,
    format_saved,
    larger_warning,
)

from . import theme
from .widgets import (
    Card, KeyValueList, Kpi, blank_image, danger_button, label, primary_button, secondary_button, shorten_middle,
)

COMPARE_BOX = (520, 250)
MODE_ORIGINAL, MODE_OPTIMIZED, MODE_BOTH = "ORIGINAL", "OPTIMIZADA", "LADO A LADO"


@dataclass
class ResultActions:
    """Acciones que la ventana principal implementa y esta tarjeta dispara."""

    open_file: Callable[[Path], None]
    open_folder: Callable[[Path], None]
    copy_path: Callable[[Path], None]
    discard: Callable[[Path], None]
    show_failed: Callable[[], None]
    select_compare: Callable[[int], None]


def _fit(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    fitted = image.copy()
    fitted.thumbnail(box, Image.LANCZOS)
    return fitted


class CompareView(ctk.CTkFrame):
    """ORIGINAL | OPTIMIZADA | LADO A LADO con miniaturas (la conversión real es independiente)."""

    def __init__(self, master, on_select: Callable[[int], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_select = on_select
        self._original: Image.Image | None = None
        self._optimized: Image.Image | None = None
        self._captions = ("", "")
        self._ctk_image: ctk.CTkImage | None = None
        self.grid_columnconfigure(0, weight=1)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._mode = ctk.CTkSegmentedButton(
            controls, values=[MODE_ORIGINAL, MODE_OPTIMIZED, MODE_BOTH], command=lambda _v: self._render(),
            font=theme.mono_font(11, "bold"), **theme.SEGMENTED,
        )
        self._mode.set(MODE_BOTH)
        self._mode.pack(side="left")
        self._names = ctk.CTkOptionMenu(
            controls, values=["-"], command=self._names_changed, width=200, font=theme.ui_font(12),
            dropdown_font=theme.ui_font(12), **theme.OPTION_MENU,
        )
        self._name_values: list[str] = []

        self._picture = ctk.CTkLabel(
            self, text="", fg_color=theme.CARD_ALT, corner_radius=6, width=COMPARE_BOX[0], height=COMPARE_BOX[1],
            font=theme.mono_font(11), text_color=theme.MUTED,
        )
        self._picture.grid(row=1, column=0, sticky="w")
        self._caption = label(self, "", size=11, color=theme.MUTED, mono=True, wraplength=COMPARE_BOX[0])
        self._caption.grid(row=2, column=0, sticky="w", pady=(4, 0))

    def set_names(self, names: list[str], selected: int = 0) -> None:
        """Selector de imagen (solo en lotes)."""
        self._name_values = names
        shown = [shorten_middle(name, 30) for name in names]
        self._names.configure(values=shown)
        self._names.set(shown[selected])
        self._names.pack(side="left", padx=(10, 0))

    def hide_names(self) -> None:
        self._names.pack_forget()

    def _names_changed(self, value: str) -> None:
        shown = [shorten_middle(name, 30) for name in self._name_values]
        if value in shown:
            self._on_select(shown.index(value))

    def show_message(self, text: str) -> None:
        self._original = self._optimized = None
        self._ctk_image = None
        self._picture.configure(image=blank_image(), text=text)
        self._caption.configure(text="")

    def show(self, original: Image.Image, optimized: Image.Image, original_caption: str, optimized_caption: str) -> None:
        self._original, self._optimized = original, optimized
        self._captions = (original_caption, optimized_caption)
        self._render()

    def _render(self) -> None:
        if self._original is None or self._optimized is None:
            return
        mode = self._mode.get()
        if mode == MODE_ORIGINAL:
            picture, caption = _fit(self._original, COMPARE_BOX), f"ORIGINAL · {self._captions[0]}"
        elif mode == MODE_OPTIMIZED:
            picture, caption = _fit(self._optimized, COMPARE_BOX), f"OPTIMIZADA · {self._captions[1]}"
        else:
            half = (COMPARE_BOX[0] // 2 - 4, COMPARE_BOX[1])
            picture = Image.new("RGB", COMPARE_BOX, theme.CARD_ALT)
            for index, source in enumerate((self._original, self._optimized)):
                fitted = _fit(source, half)
                x = index * (half[0] + 8) + (half[0] - fitted.width) // 2
                picture.paste(fitted, (x, (COMPARE_BOX[1] - fitted.height) // 2))
            caption = f"ORIGINAL · {self._captions[0]}\nOPTIMIZADA · {self._captions[1]}"
        self._ctk_image = ctk.CTkImage(light_image=picture, dark_image=picture, size=picture.size)
        self._picture.configure(image=self._ctk_image, text="")
        self._caption.configure(text=caption)


class ResultPanel(Card):
    def __init__(self, master, actions: ResultActions) -> None:
        super().__init__(master, "RESULT")
        self._actions = actions
        self._content: ctk.CTkFrame | None = None
        self._compare: CompareView | None = None
        self.last_output: Path | None = None  # archivo (o carpeta del lote) al que apuntan los botones

    # ------------------------------------------------------------------ helpers --
    def _reset(self, title: str, color: str) -> ctk.CTkFrame:
        if self._content is not None:
            self._content.destroy()
        self._compare = None
        self.title_label.configure(text=title, text_color=color)
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid(row=1, column=0, padx=theme.INNER, pady=(0, theme.INNER), sticky="ew")
        return self._content

    def hide(self) -> None:
        if self._content is not None:
            self._content.destroy()
            self._content = None
        self._compare = None
        self.last_output = None
        self.grid_remove()

    def _kpis(self, parent, stats: CompressionStats, elapsed: float | None, total: bool = False) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        larger = stats.is_larger
        tiles = [
            ("ANTES", format_bytes(stats.before), "", theme.TEXT),
            ("DESPUÉS", format_bytes(stats.after), "", theme.TEXT),
            ("AHORRADO" if not larger else "AUMENTO", format_saved(stats), "más grande" if larger else "",
             theme.WARN if larger else theme.ACCENT),
            ("REDUCCIÓN", format_reduction(stats), "espacio ahorrado" if not larger else "negativa",
             theme.WARN if larger else theme.ACCENT),
            ("RATIO", format_ratio(stats), "original : final", theme.TEXT),
            ("TIEMPO TOTAL" if total else "TIEMPO", format_elapsed(elapsed), format_clock(elapsed), theme.TEXT),
        ]
        for column, (title, value, note, color) in enumerate(tiles):
            row.grid_columnconfigure(column, weight=1, uniform="kpi")
            tile = Kpi(row, title)
            tile.set(value, note, color)
            tile.grid(row=0, column=column, padx=3, sticky="ew")
        return row

    def _buttons(self, parent, target: Path | None, *, file: bool, folder: Path | None, discard: bool,
                 failed: bool = False) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        column = 0

        def add(button: ctk.CTkButton) -> None:
            nonlocal column
            button.grid(row=0, column=column, padx=(0, 8))
            column += 1

        if file and target is not None:
            add(primary_button(row, "ABRIR ARCHIVO", lambda: self._actions.open_file(target), height=34))
        if folder is not None:
            add(secondary_button(row, "ABRIR CARPETA", lambda: self._actions.open_folder(folder), height=34))
        if target is not None:
            add(secondary_button(row, "COPIAR RUTA", lambda: self._actions.copy_path(target), height=34))
        if failed:
            add(secondary_button(row, "VER ARCHIVOS FALLIDOS", self._actions.show_failed, height=34))
        if discard and target is not None:
            add(danger_button(row, "DESCARTAR RESULTADO", lambda: self._actions.discard(target), height=34))
        return row

    def _stack(self, parent, *widgets, pady=(8, 0)) -> None:
        for index, widget in enumerate(widgets):
            if widget is not None:
                widget.grid(row=index, column=0, sticky="ew", pady=pady if index else 0)

    # -------------------------------------------------------------------- video --
    def show_video(self, result: ConversionResult) -> CompressionStats:
        stats = CompressionStats(result.source_bytes or 0, result.output_bytes or 0)
        larger = stats.is_larger
        content = self._reset(
            "⚠ CONVERSIÓN COMPLETA — EL ARCHIVO ES MÁS GRANDE" if larger else "✓ CONVERSIÓN COMPLETA",
            theme.WARN if larger else theme.ACCENT,
        )
        src_w, src_h = result.source_size or result.output_size
        out_w, out_h = result.output_size
        audio_in = result.source_audio_codec if result.has_source_audio else "NINGUNO"
        audio_out = result.audio_codec if result.has_source_audio else "NINGUNO"

        columns = ctk.CTkFrame(content, fg_color="transparent")
        columns.grid_columnconfigure((0, 1), weight=1, uniform="io")
        for column, (title, rows) in enumerate((
            ("INPUT", [("Resolución", f"{src_w}×{src_h}"), ("Códec", result.source_video_codec or "—"),
                       ("Audio", audio_in or "—"), ("Tamaño", format_bytes(stats.before))]),
            ("OUTPUT", [("Resolución", f"{out_w}×{out_h}"),
                        ("Códec", f"{result.video_codec}" + (f" + {audio_out}" if result.has_source_audio else "")),
                        ("Audio", audio_out or "—"), ("Tamaño", format_bytes(stats.after))]),
        )):
            block = ctk.CTkFrame(columns, fg_color=theme.CARD_ALT, corner_radius=6, border_width=1,
                                 border_color=theme.BORDER)
            block.grid(row=0, column=column, padx=3, sticky="nsew")
            label(block, title, size=10, color=theme.MUTED, mono=True).grid(row=0, column=0, padx=10, pady=(8, 2),
                                                                           sticky="w")
            rows_view = KeyValueList(block)
            rows_view.set_rows(rows)
            rows_view.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="w")

        summary = label(
            content,
            f"{src_w}×{src_h} → {out_w}×{out_h}   ·   {result.source_video_codec or '—'} → {result.video_codec}"
            f"   ·   {(audio_in + ' → ' + audio_out) if result.has_source_audio else 'AUDIO: NINGUNO'}"
            f"   ·   {result.quality_label}",
            size=12, mono=True, wraplength=860,
        )
        notice = None
        if (message := larger_warning(stats)):
            notice = label(content, f"⚠ {message} Se conservó el archivo; puedes descartarlo con el botón de abajo.",
                           size=12, color=theme.WARN, wraplength=860)
        saved_to = label(content, f"Guardado en: {display_path(result.output_path)}", size=11, color=theme.MUTED,
                         mono=True, wraplength=860)
        self.last_output = result.output_path
        self._stack(
            content, columns, summary, self._kpis(content, stats, result.elapsed_seconds), notice, saved_to,
            self._buttons(content, result.output_path, file=True, folder=result.output_path.parent, discard=True),
        )
        return stats

    # -------------------------------------------------------------------- image --
    def show_image(self, result: ConversionResult) -> CompressionStats:
        stats = CompressionStats(result.source_bytes or 0, result.output_bytes or 0)
        larger = stats.is_larger
        content = self._reset(
            "⚠ CONVERSIÓN COMPLETA — EL ARCHIVO ES MÁS GRANDE" if larger else "✓ CONVERSIÓN COMPLETA",
            theme.WARN if larger else theme.ACCENT,
        )
        src_w, src_h = result.source_size or result.output_size
        out_w, out_h = result.output_size

        body = ctk.CTkFrame(content, fg_color="transparent")
        body.grid_columnconfigure(1, weight=1)
        self._compare = CompareView(body, self._actions.select_compare)
        self._compare.grid(row=0, column=0, sticky="nw")
        self._compare.show_message("Generando vista previa...")
        details = KeyValueList(body)
        details.set_rows([
            ("Resolución", f"{src_w}×{src_h} → {out_w}×{out_h}"),
            ("Tamaño", f"{format_bytes(stats.before)} → {format_bytes(stats.after)}"),
            ("Formato", f"{result.source_format or '—'} → {result.format_label}"),
            ("Reducción", format_reduction(stats)),
            ("Archivo", result.output_path.name),
        ])
        details.grid(row=0, column=1, padx=(18, 0), sticky="nw")

        notices = [f"⚠ {warning}" for warning in result.warnings]
        if (message := larger_warning(stats)):
            notices.append(f"⚠ {message} Se conservó el archivo; puedes descartarlo con el botón de abajo.")
        notice = label(content, "\n".join(notices), size=12, color=theme.WARN, wraplength=860) if notices else None
        saved_to = label(content, f"Guardado en: {display_path(result.output_path)}", size=11, color=theme.MUTED,
                         mono=True, wraplength=860)
        self.last_output = result.output_path
        self._stack(
            content, body, self._kpis(content, stats, result.elapsed_seconds), notice, saved_to,
            self._buttons(content, result.output_path, file=True, folder=result.output_path.parent, discard=True),
        )
        return stats

    # -------------------------------------------------------------------- batch --
    def show_batch(self, batch: BatchResult) -> CompressionStats:
        stats = CompressionStats(batch.source_bytes_total, batch.output_bytes_total)
        ok, failed = len(batch.succeeded), len(batch.failed)
        if batch.cancelled:
            title, color = "⚠ LOTE CANCELADO", theme.WARN
        elif ok == 0:
            title, color = "✗ LOTE FALLIDO", theme.ERROR
        elif failed:
            title, color = "⚠ LOTE COMPLETO CON ERRORES", theme.WARN
        else:
            title, color = "✓ LOTE COMPLETO", theme.ACCENT
        content = self._reset(title, color)

        rows = [("Procesadas", str(ok)), ("Fallidas", str(failed))]
        if batch.cancelled:
            rows.append(("Sin procesar", str(batch.total - batch.processed)))
        rows.append(("Carpeta", display_path(batch.output_dir) + "/"))
        counts = KeyValueList(content, value_limit=70)
        counts.set_rows(rows)

        compare = None
        if ok:
            self._compare = CompareView(content, self._actions.select_compare)
            self._compare.set_names([item.source_path.name if item.source_path else item.output_path.name
                                     for item in batch.succeeded])
            self._compare.show_message("Generando vista previa...")
            compare = self._compare

        notices = []
        if ok and (message := larger_warning(stats)):
            notices.append(f"⚠ {message}")
        notice = label(content, "\n".join(notices), size=12, color=theme.WARN, wraplength=860) if notices else None
        kpis = self._kpis(content, stats, batch.elapsed_seconds, total=True) if ok else None
        self.last_output = batch.output_dir
        self._stack(
            content, counts, kpis, notice, compare,
            self._buttons(content, batch.output_dir, file=False, folder=batch.output_dir, discard=False,
                          failed=bool(failed)),
        )
        return stats

    # -------------------------------------------------------------------- error --
    def show_error(self, message: str, details: str | None = None, title: str = "ERROR DE CONVERSIÓN") -> None:
        content = self._reset(f"✗ {title}", theme.ERROR)
        widgets = [label(content, message, size=13, wraplength=860)]
        if details:
            widgets.append(label(content, f"Detalles técnicos:\n{details}", size=11, color=theme.MUTED, mono=True,
                                 wraplength=860))
        widgets.append(label(content, "El detalle completo está en logs/app.log.", size=11, color=theme.MUTED))
        self.last_output = None
        self._stack(content, *widgets, pady=(6, 0))

    # ------------------------------------------------------------------ compare --
    def set_compare_images(self, original: Image.Image, optimized: Image.Image, original_caption: str,
                           optimized_caption: str) -> None:
        if self._compare is not None:
            self._compare.show(original, optimized, original_caption, optimized_caption)

    def set_compare_message(self, text: str) -> None:
        if self._compare is not None:
            self._compare.show_message(text)
