"""Tarjeta OUTPUT CONFIGURATION: perfil/reducción, formato, vista previa de resolución y avisos.

Contiene toda la lógica de ajustes (qué se muestra, qué es válido y qué advertir); la ventana
principal solo pide ``evaluate()`` y recibe un ``ConversionSettings`` ya validado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import customtkinter as ctk

from services.common import ConversionError, calculate_output_size, describe_scale, parse_percentage
from services.ffmpeg_service import AVI_NOTE, VIDEO_PROFILES
from services.image_processor import DEFAULT_IMAGE_QUALITY, IMAGE_FORMATS, parse_image_quality
from services.metadata_service import IMAGE_KIND, VIDEO_KIND
from services.video_profiles import (
    CRF_GUIDE,
    CRF_HIGH_WARNING,
    CRF_MAX,
    CRF_MIN,
    CRF_SEVERE_FROM,
    CRF_SEVERE_WARNING,
    CRF_WARNING_ABOVE,
    DEFAULT_PRESET,
    DEFAULT_PROFILE,
    PRESETS,
    PROFILE_BY_LABEL,
    PROFILES,
    STRONG_REDUCTION_FROM,
    STRONG_REDUCTION_WARNING,
    VideoQuality,
    make_quality,
    parse_crf,
)

from . import theme
from .widgets import Card, KeyValueList, label, secondary_button

LOW_IMAGE_QUALITY = 60
SHOW_ADVANCED = "MOSTRAR OPCIONES AVANZADAS"
HIDE_ADVANCED = "OCULTAR OPCIONES AVANZADAS"


@dataclass(frozen=True)
class ConversionSettings:
    """Ajustes ya validados que recibe el worker de conversión."""

    kind: str
    format_label: str
    percent: float | None = None  # imágenes
    quality: VideoQuality | None = None  # video
    image_quality: int | None = None  # JPG/WEBP (modo avanzado)


def _crf_labels() -> list[str]:
    return [f"{crf} — {CRF_GUIDE[crf]}" if crf in CRF_GUIDE else str(crf) for crf in range(CRF_MIN, CRF_MAX + 1)]


def _entry(master, variable, width: int = 70) -> ctk.CTkEntry:
    return ctk.CTkEntry(master, textvariable=variable, width=width, justify="center", font=theme.mono_font(12),
                        **theme.ENTRY)


class ConfigPanel(Card):
    def __init__(self, master, on_change: Callable[[], None]) -> None:
        super().__init__(master, "OUTPUT CONFIGURATION")
        self._on_change = on_change
        self._kind: str | None = None
        self._size: tuple[int, int] | None = None
        self._has_audio: bool | None = None
        self._has_alpha: bool | None = None
        self._example_name: str | None = None
        self._ffmpeg_error: str | None = None
        self._advanced_open = False
        self._enabled = True

        self._image_percent = ctk.StringVar(value="50")
        self._video_reduction = ctk.StringVar(value="0")
        self._profile = ctk.StringVar(value=PROFILES[DEFAULT_PROFILE].label)
        self._crf = ctk.StringVar(value="20 — Alta calidad")
        self._preset = ctk.StringVar(value=DEFAULT_PRESET)
        self._max_width = ctk.StringVar(value="")
        self._max_height = ctk.StringVar(value="")
        self._image_quality = ctk.StringVar(value=str(DEFAULT_IMAGE_QUALITY))
        for variable in (self._image_percent, self._video_reduction, self._crf, self._preset, self._max_width,
                         self._max_height, self._image_quality):
            variable.trace_add("write", lambda *_: self._refresh())

        self._build()

    # ------------------------------------------------------------------ layout --
    def _build(self) -> None:
        # Perfil (solo video)
        self._profile_box = ctk.CTkFrame(self, fg_color="transparent")
        self._profile_box.grid_columnconfigure(0, weight=1)
        label(self._profile_box, "PERFIL DE CALIDAD", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, sticky="w")
        self._profile_menu = ctk.CTkOptionMenu(
            self._profile_box, values=[p.label for p in PROFILES.values()], variable=self._profile,
            command=lambda _v: self._refresh(), width=230, font=theme.ui_font(13), dropdown_font=theme.ui_font(13),
            **theme.OPTION_MENU,
        )
        self._profile_menu.grid(row=1, column=0, sticky="w", pady=(2, 2))
        self._profile_desc = label(self._profile_box, "", size=12, color=theme.MUTED, wraplength=440)
        self._profile_desc.grid(row=2, column=0, sticky="w")

        # Reducción (solo imagen)
        self._percent_box = ctk.CTkFrame(self, fg_color="transparent")
        label(self._percent_box, "REDUCCIÓN (%)", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, columnspan=6, sticky="w")
        self._percent_entry = _entry(self._percent_box, self._image_percent)
        self._percent_entry.grid(row=1, column=0, pady=(2, 0), sticky="w")
        label(self._percent_box, "%", color=theme.MUTED).grid(row=1, column=1, padx=(4, 10), pady=(2, 0))
        self._preset_buttons: list[ctk.CTkButton] = []
        for column, preset in enumerate((25, 50, 75), start=2):
            button = secondary_button(
                self._percent_box, f"{preset} %", lambda value=preset: self._image_percent.set(str(value)),
                width=58, height=30,
            )
            button.grid(row=1, column=column, padx=3, pady=(2, 0))
            self._preset_buttons.append(button)

        # Formato (siempre)
        self._format_box = ctk.CTkFrame(self, fg_color="transparent")
        label(self._format_box, "FORMATO DE SALIDA", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, sticky="w")
        self._format_selector = ctk.CTkSegmentedButton(
            self._format_box, values=["-"], command=lambda _v: self._refresh(), font=theme.mono_font(12, "bold"),
            **theme.SEGMENTED,
        )
        self._format_selector.grid(row=1, column=0, pady=(2, 0), sticky="w")
        self._format_hint = label(self._format_box, "", size=11, color=theme.MUTED, wraplength=440)
        self._format_hint.grid(row=2, column=0, sticky="w", pady=(2, 0))

        # Personalizado (solo video): reducción + CRF
        self._custom_box = ctk.CTkFrame(self, fg_color="transparent")
        label(self._custom_box, "REDUCCIÓN DE RESOLUCIÓN", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, sticky="w")
        label(self._custom_box, "CRF (CALIDAD H.264)", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=1, sticky="w", padx=(18, 0))
        reduction_row = ctk.CTkFrame(self._custom_box, fg_color="transparent")
        reduction_row.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._reduction_entry = _entry(reduction_row, self._video_reduction)
        self._reduction_entry.pack(side="left")
        label(reduction_row, "%  (0 = conservar)", color=theme.MUTED, size=11).pack(side="left", padx=(6, 0))
        self._crf_menu = ctk.CTkOptionMenu(
            self._custom_box, values=_crf_labels(), variable=self._crf, width=190, font=theme.ui_font(13),
            dropdown_font=theme.ui_font(13), **theme.OPTION_MENU,
        )
        self._crf_menu.grid(row=1, column=1, sticky="w", padx=(18, 0), pady=(2, 0))

        # Avanzado
        self._advanced_button = secondary_button(self, SHOW_ADVANCED, self._toggle_advanced, height=28)
        self._advanced_box = ctk.CTkFrame(self, fg_color=theme.CARD_ALT, corner_radius=6, border_width=1,
                                          border_color=theme.BORDER)
        self._video_advanced = ctk.CTkFrame(self._advanced_box, fg_color="transparent")
        self._video_advanced.grid(row=0, column=0, padx=10, pady=8, sticky="w")
        label(self._video_advanced, "PRESET X264", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, sticky="w")
        label(self._video_advanced, "ANCHO MÁX.", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=1, sticky="w", padx=(16, 0))
        label(self._video_advanced, "ALTO MÁX.", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=2, sticky="w", padx=(10, 0))
        self._preset_menu = ctk.CTkOptionMenu(
            self._video_advanced, values=list(PRESETS), variable=self._preset, width=110, font=theme.ui_font(13),
            dropdown_font=theme.ui_font(13), **theme.OPTION_MENU,
        )
        self._preset_menu.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._width_entry = _entry(self._video_advanced, self._max_width, 80)
        self._width_entry.grid(row=1, column=1, padx=(16, 0), pady=(2, 0), sticky="w")
        self._height_entry = _entry(self._video_advanced, self._max_height, 80)
        self._height_entry.grid(row=1, column=2, padx=(10, 0), pady=(2, 0), sticky="w")
        label(self._video_advanced,
              "Preset más lento = archivo más pequeño. Ancho/alto máx.: vacío = sin límite; "
              "se conserva la relación de aspecto y nunca se amplía.",
              size=11, color=theme.MUTED, wraplength=440).grid(row=2, column=0, columnspan=3, sticky="w",
                                                              pady=(4, 0))
        self._image_advanced = ctk.CTkFrame(self._advanced_box, fg_color="transparent")
        self._image_advanced.grid(row=0, column=0, padx=10, pady=8, sticky="w")
        label(self._image_advanced, "CALIDAD (1–100)", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, sticky="w")
        self._quality_entry = _entry(self._image_advanced, self._image_quality)
        self._quality_entry.grid(row=1, column=0, pady=(2, 0), sticky="w")
        label(self._image_advanced, "Solo JPG y WEBP (formatos con pérdida). 95 es el valor por defecto.",
              size=11, color=theme.MUTED, wraplength=440).grid(row=2, column=0, sticky="w", pady=(4, 0))

        # Vista previa de resolución y resumen
        self._preview_box = ctk.CTkFrame(self, fg_color=theme.CARD_ALT, corner_radius=6, border_width=1,
                                         border_color=theme.BORDER)
        self._preview_box.grid_columnconfigure(0, weight=1)
        label(self._preview_box, "ORIGINAL → SALIDA", size=10, color=theme.MUTED, mono=True).grid(
            row=0, column=0, padx=12, pady=(8, 0), sticky="w")
        self._resolution_label = label(self._preview_box, "—", size=17, weight="bold", mono=True, color=theme.ACCENT)
        self._resolution_label.grid(row=1, column=0, padx=12, sticky="w")
        self._scale_label = label(self._preview_box, "", size=11, color=theme.MUTED, mono=True, wraplength=430)
        self._scale_label.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")
        self._summary = KeyValueList(self, value_limit=44)
        self._warnings = label(self, "", size=12, color=theme.WARN, wraplength=460)

        self._placeholder = label(self, "Carga un video o una imagen para configurar la salida.", size=12,
                                  color=theme.MUTED, mono=True, wraplength=440)
        self.add(self._placeholder, pady=(0, theme.INNER))
        self._widgets_in_order = [
            self._profile_box, self._percent_box, self._format_box, self._custom_box, self._advanced_button,
            self._advanced_box, self._preview_box, self._summary, self._warnings,
        ]
        for widget in self._widgets_in_order:
            self.add(widget, pady=(0, 8))
            widget.grid_remove()

    # ---------------------------------------------------------------- public API --
    def set_media(
        self,
        kind: str,
        size: tuple[int, int],
        formats: list[str],
        preferred_format: str,
        has_audio: bool | None = None,
        has_alpha: bool | None = None,
        example_name: str | None = None,
    ) -> None:
        """Prepara los controles para el archivo cargado (``example_name`` solo en lotes)."""
        if self._kind != kind:  # valores por defecto propios de cada tipo
            self._image_percent.set("50")
            self._video_reduction.set("0")
        self._kind, self._size, self._has_audio, self._has_alpha = kind, size, has_audio, has_alpha
        self._example_name = example_name
        self._format_selector.configure(values=formats)
        self._format_selector.set(preferred_format if preferred_format in formats else formats[0])
        self._advanced_open = False
        self._advanced_button.configure(text=SHOW_ADVANCED)
        self._refresh()

    def clear(self) -> None:
        self._kind = self._size = None
        for widget in self._widgets_in_order:
            widget.grid_remove()
        self._placeholder.grid()

    def set_ffmpeg_error(self, message: str | None) -> None:
        self._ffmpeg_error = message
        self._refresh()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        state = "normal" if enabled else "disabled"
        for widget in (self._profile_menu, self._percent_entry, self._format_selector, self._reduction_entry,
                       self._crf_menu, self._advanced_button, self._preset_menu, self._width_entry,
                       self._height_entry, self._quality_entry, *self._preset_buttons):
            widget.configure(state=state)

    @property
    def format_label(self) -> str:
        return self._format_selector.get()

    def evaluate(self) -> tuple[ConversionSettings | None, str | None]:
        """``(ajustes, None)`` si todo es válido, o ``(None, motivo)``."""
        if self._kind is None:
            return None, "Carga un archivo para comenzar."
        try:
            if self._kind == VIDEO_KIND:
                if self._ffmpeg_error:
                    return None, self._ffmpeg_error
                return ConversionSettings(VIDEO_KIND, self.format_label, quality=self._video_quality()), None
            percent = parse_percentage(self._image_percent.get())
            image_quality = None
            if self._advanced_open and self._format_is_lossy():
                image_quality = parse_image_quality(self._image_quality.get())
            return ConversionSettings(IMAGE_KIND, self.format_label, percent=percent, image_quality=image_quality), None
        except ConversionError as exc:
            return None, str(exc)

    # --------------------------------------------------------------- internals --
    def _profile_key(self) -> str:
        return PROFILE_BY_LABEL[self._profile.get()].key

    def _video_quality(self) -> VideoQuality:
        key = self._profile_key()
        if key == "custom" and self._advanced_open:
            return make_quality(key, self._video_reduction.get(), self._crf.get(), self._preset.get(),
                                self._max_width.get(), self._max_height.get())
        return make_quality(key, self._video_reduction.get(), self._crf.get())

    def _format_is_lossy(self) -> bool:
        fmt = IMAGE_FORMATS.get(self.format_label)
        return bool(fmt and fmt.lossy)

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        self._advanced_button.configure(text=HIDE_ADVANCED if self._advanced_open else SHOW_ADVANCED)
        self._refresh()

    def _show(self, widget, visible: bool) -> None:
        if visible:
            widget.grid()
        else:
            widget.grid_remove()

    def _refresh(self) -> None:
        if self._kind is None or self._size is None:
            return
        is_video = self._kind == VIDEO_KIND
        custom = is_video and self._profile_key() == "custom"
        self._placeholder.grid_remove()
        if is_video and not custom:
            self._advanced_open = False  # lo avanzado de video solo existe en «Personalizado»
        lossy_image = not is_video and self._format_is_lossy()
        self._show(self._profile_box, is_video)
        self._show(self._percent_box, not is_video)
        self._show(self._format_box, True)
        self._show(self._custom_box, custom)
        self._show(self._advanced_button, custom or (not is_video and lossy_image))
        self._show(self._advanced_box, self._advanced_open and (custom or lossy_image))
        self._show(self._preview_box, True)
        self._show(self._summary, True)
        self._show(self._video_advanced, is_video)
        self._show(self._image_advanced, not is_video)
        self._advanced_button.configure(text=HIDE_ADVANCED if self._advanced_open else SHOW_ADVANCED)

        settings, error = self.evaluate()
        warnings: list[tuple[str, str]] = []  # (nivel, texto)
        if is_video:
            self._refresh_video(settings, error, warnings)
        else:
            self._refresh_image(settings, error, warnings)
        self._show_warnings(warnings)
        self._on_change()

    def _show_warnings(self, warnings: list[tuple[str, str]]) -> None:
        if not warnings:
            self._warnings.configure(text="")
            self._show(self._warnings, False)
            return
        icons = {"error": "✗", "warn": "⚠", "info": "ℹ"}
        text = "\n".join(f"{icons[level]} {message}" for level, message in warnings)
        worst = "error" if any(level == "error" for level, _ in warnings) else (
            "warn" if any(level == "warn" for level, _ in warnings) else "info")
        color = {"error": theme.ERROR, "warn": theme.WARN, "info": theme.MUTED}[worst]
        self._warnings.configure(text=text, text_color=color)
        self._show(self._warnings, True)

    def _set_resolution(self, source: tuple[int, int], target: tuple[int, int], prefix: str = "") -> None:
        linear, pixels = describe_scale(source, target)
        self._resolution_label.configure(
            text=f"{prefix}{source[0]} × {source[1]} → {target[0]} × {target[1]}", text_color=theme.ACCENT)
        if target == source:
            self._scale_label.configure(text="100% resolución (se conserva)")
        else:
            self._scale_label.configure(text=f"{linear:.1f}% resolución lineal · {pixels:.1f}% de los píxeles")

    def _refresh_video(self, settings, error, warnings) -> None:
        assert self._size is not None
        key = self._profile_key()
        info = PROFILE_BY_LABEL[self._profile.get()]
        self._profile_desc.configure(text=info.description)
        formats = self._format_selector.get()
        profile = VIDEO_PROFILES.get(formats)
        self._format_hint.configure(text=f"{profile.description}\n{AVI_NOTE}" if profile else "")

        if info.warning:
            warnings.append(("warn", info.warning))
        if error:
            warnings.append(("error", error))
            self._resolution_label.configure(text="—", text_color=theme.FAINT)
            self._scale_label.configure(text="")
            self._summary.set_rows([("Perfil", info.label)])
            return
        quality = settings.quality
        target = quality.target_size(*self._size)
        self._set_resolution(self._size, target)
        reduction = (1 - target[0] / self._size[0]) * 100
        if reduction >= STRONG_REDUCTION_FROM:
            warnings.append(("warn", STRONG_REDUCTION_WARNING))
        if key == "custom":
            if quality.crf >= CRF_SEVERE_FROM:
                warnings.append(("warn", CRF_SEVERE_WARNING))
            elif quality.crf > CRF_WARNING_ABOVE:
                warnings.append(("warn", CRF_HIGH_WARNING))
            if (quality.max_width and quality.max_width >= self._size[0]) or (
                    quality.max_height and quality.max_height >= self._size[1]):
                warnings.append(("info", "Los límites de ancho/alto no amplían el video: se respeta la resolución original."))
        if key == "web" and target == (self._size[0] - self._size[0] % 2, self._size[1] - self._size[1] % 2):
            warnings.append(("info", "El video ya es igual o menor a 1280×720: no se amplía."))

        guide = CRF_GUIDE.get(quality.crf)
        self._summary.set_rows([
            ("Perfil", quality.label),
            ("Calidad", f"CRF {quality.crf}" + (f" — {guide}" if guide else "") + f" · preset {quality.preset}"),
            ("Códec", "H.264 (libx264) · yuv420p"),
            ("Audio", self._audio_text(quality)),
            ("Contenedor", f"{self.format_label} · faststart"),
        ])

    def _audio_text(self, quality: VideoQuality) -> str:
        if self._has_audio is False:
            return "NINGUNO (el original no tiene audio)"
        if self._has_audio is None:
            return f"AAC {quality.audio_bitrate} (si el original lo tiene)"
        return f"AAC {quality.audio_bitrate.replace('k', ' kbps')} (se preserva)"

    def _refresh_image(self, settings, error, warnings) -> None:
        assert self._size is not None
        fmt_label = self.format_label
        fmt = IMAGE_FORMATS.get(fmt_label)
        self._format_hint.configure(text="")
        if error:
            warnings.append(("error", error))
            self._resolution_label.configure(text="—", text_color=theme.FAINT)
            self._scale_label.configure(text="")
            self._summary.set_rows([("Formato", fmt_label)])
            return
        percent = settings.percent
        target = calculate_output_size(self._size[0], self._size[1], percent)
        self._set_resolution(self._size, target)
        if self._example_name:
            self._scale_label.configure(
                text=self._scale_label.cget("text")
                + f"\nEjemplo con «{self._example_name}»: cada imagen se calcula por separado.")
        if percent >= STRONG_REDUCTION_FROM:
            warnings.append(("warn", STRONG_REDUCTION_WARNING))
        quality_text = "sin pérdida"
        if fmt and fmt.lossy:
            quality = settings.image_quality or DEFAULT_IMAGE_QUALITY
            quality_text = f"con pérdida · calidad {quality}"
            if quality < LOW_IMAGE_QUALITY:
                warnings.append(("warn", "Esta configuración puede producir una pérdida considerable de calidad."))
        if fmt and not fmt.supports_alpha and self._has_alpha:
            warnings.append(("info", f"{fmt_label} no admite transparencia: se usará fondo blanco."))
        self._summary.set_rows([
            ("Reducción", f"{percent:g}% por lado (conserva {100 - percent:g}%)"),
            ("Formato", f"{fmt_label} · {quality_text}"),
            ("Aspecto", "se conserva · sin recortes"),
        ])
