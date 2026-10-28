"""Ventana principal (CustomTkinter). Orquesta los servicios sin contener lógica de OpenCV."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from services.common import (
    OUTPUT_DIR,
    BatchResult,
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    calculate_output_size,
    display_path,
    ensure_output_dir,
    format_bytes,
    format_duration,
    parse_percentage,
)
from services.image_processor import (
    IMAGE_INPUT_EXTENSIONS,
    available_image_formats,
    convert_images,
    image_size,
    load_image,
    to_rgb_preview,
)
from services.ffmpeg_service import (
    AVI_NOTE,
    FFMPEG_READY_MESSAGE,
    FFMPEG_READY_NO_AUDIO_MESSAGE,
    VIDEO_PROFILES,
    ffmpeg_status,
)
from services.video_processor import (
    STAGE_ENCODING,
    STAGE_PREPARING,
    STAGE_VALIDATING,
    VIDEO_INPUT_EXTENSIONS,
    VideoInfo,
    available_video_formats,
    convert_video,
    probe_video,
)
from services.video_profiles import (
    COMPRESSION_WARNING,
    CRF_GUIDE,
    CRF_HIGH_WARNING,
    CRF_MAX,
    CRF_MIN,
    CRF_WARNING_ABOVE,
    DEFAULT_PROFILE,
    PROFILE_BY_LABEL,
    PROFILES,
    VideoQuality,
    make_quality,
    parse_crf,
)

logger = logging.getLogger(__name__)

PREVIEW_MAX_SIZE = (340, 230)
POLL_INTERVAL_MS = 100
ERROR_COLOR = ("#c0262d", "#ff6b6b")
OK_COLOR = ("#1a7f37", "#4ade80")
WARN_COLOR = ("#9a6700", "#fbbf24")

IMAGE_KIND = "imagen"
VIDEO_KIND = "video"


def _pattern(extensions: tuple[str, ...]) -> str:
    return " ".join(f"*{ext}" for ext in extensions)


def _crf_labels() -> list[str]:
    """Opciones del selector de CRF: «20 — Alta calidad», «22», ..."""
    return [f"{crf} — {CRF_GUIDE[crf]}" if crf in CRF_GUIDE else str(crf) for crf in range(CRF_MIN, CRF_MAX + 1)]


class MainWindow(ctk.CTk):
    """Ventana de la aplicación: carga archivos, pide parámetros y lanza la conversión."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title("Conversor Multimedia")
        height = min(980, self.winfo_screenheight() - 90)  # cabe también en pantallas de 768 px
        self.geometry(f"1000x{height}")
        self.minsize(900, min(660, height))

        self._source_path: Path | None = None
        self._kind: str | None = None
        self._media_size: tuple[int, int] | None = None
        self._busy = False
        self._batch_paths: list[Path] = []  # vacío cuando hay un solo archivo cargado
        self._preview_path: Path | None = None  # imagen usada como vista previa del lote
        self._output_folder: Path = OUTPUT_DIR  # carpeta que abre «ABRIR CARPETA DE SALIDA»
        self._failed_files: list[tuple[Path, str]] = []
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._events: queue.Queue[tuple] = queue.Queue()
        self._preview_image: ctk.CTkImage | None = None
        self._indeterminate = False

        self._video_has_audio: bool | None = None
        self._percent_default_kind: str | None = None  # tipo de archivo para el que se fijó el valor por defecto
        self._percent_var = ctk.StringVar(value="50")
        self._percent_var.trace_add("write", lambda *_: self._refresh_summary())
        self._profile_var = ctk.StringVar(value=PROFILES[DEFAULT_PROFILE].label)
        self._crf_var = ctk.StringVar(value="20 — Alta calidad")

        # FFmpeg se verifica una sola vez al arrancar; sin él no se convierten videos.
        self._ffmpeg_info, self._ffmpeg_error = ffmpeg_status()

        self._build_widgets()
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            ensure_output_dir()
        except ConversionError as exc:
            self._log(str(exc), ERROR_COLOR)
        if self._ffmpeg_info is not None:
            self._log(f"FFmpeg {self._ffmpeg_info.version} detectado ({self._ffmpeg_info.origin}).")
        else:
            self._log(self._ffmpeg_error or "", ERROR_COLOR)

    # ------------------------------------------------------------------ UI --
    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(4, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(14, 4), sticky="ew")
        ctk.CTkLabel(header, text="Conversor Multimedia", font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Reduce la resolución de videos e imágenes. Todo se procesa en tu equipo y se guarda en la carpeta outs/.",
            text_color=("gray30", "gray70"),
        ).pack(anchor="w")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=1, column=0, padx=24, pady=6, sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1, uniform="load")
        big_font = ctk.CTkFont(size=18, weight="bold")
        self._video_button = ctk.CTkButton(
            buttons, text="CARGAR VIDEO", height=56, font=big_font, command=self._on_load_video
        )
        self._video_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self._image_button = ctk.CTkButton(
            buttons, text="CARGAR IMAGEN", height=56, font=big_font, command=self._on_load_image
        )
        self._image_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        self._panel = ctk.CTkFrame(self)
        self._panel.grid(row=2, column=0, padx=24, pady=6, sticky="nsew")
        self._panel.grid_columnconfigure(1, weight=1)
        self._build_panel()
        self._empty_label = ctk.CTkLabel(
            self, text="Carga un video o una imagen para comenzar.", text_color=("gray40", "gray60")
        )
        self._empty_label.grid(row=2, column=0)
        self._panel.grid_remove()

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.grid(row=3, column=0, padx=24, pady=(4, 0), sticky="ew")
        status.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(status, text="", anchor="w")
        self._status_label.grid(row=0, column=0, sticky="ew")
        self._progress = ctk.CTkProgressBar(status)
        self._progress.set(0)
        self._progress.grid(row=1, column=0, pady=(2, 0), sticky="ew")
        self._cancel_button = ctk.CTkButton(
            status, text="CANCELAR", width=110, fg_color=("gray55", "gray35"), command=self._on_cancel
        )
        self._cancel_button.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self._cancel_button.grid_remove()

        results = ctk.CTkFrame(self, fg_color="transparent")
        results.grid(row=4, column=0, padx=24, pady=(6, 14), sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(results, text="Resultados", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        self._results_box = ctk.CTkTextbox(results, height=90, state="disabled", wrap="word")
        self._results_box.grid(row=1, column=0, sticky="nsew")
        actions = ctk.CTkFrame(results, fg_color="transparent")
        actions.grid(row=2, column=0, pady=(8, 0), sticky="e")
        self._open_button = ctk.CTkButton(
            actions, text="ABRIR CARPETA DE SALIDA", height=36, command=self._open_output_folder
        )
        self._failed_button = ctk.CTkButton(
            actions, text="VER ARCHIVOS FALLIDOS", height=36, fg_color=("gray55", "gray35"),
            command=self._show_failed_files,
        )

    def _build_panel(self) -> None:
        self._preview_label = ctk.CTkLabel(
            self._panel, text="", width=PREVIEW_MAX_SIZE[0], height=PREVIEW_MAX_SIZE[1],
            fg_color=("gray85", "gray20"), corner_radius=8,
        )
        self._preview_label.grid(row=0, column=0, padx=16, pady=16, sticky="n")
        self._preview_caption = ctk.CTkLabel(
            self._panel, text="", wraplength=PREVIEW_MAX_SIZE[0], text_color=WARN_COLOR, justify="center"
        )
        self._preview_caption.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="n")

        info = ctk.CTkFrame(self._panel, fg_color="transparent")
        info.grid(row=0, column=1, padx=(0, 16), pady=(16, 0), sticky="nsew")
        info.grid_columnconfigure(0, weight=1)
        self._file_label = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480,
                                        font=ctk.CTkFont(weight="bold"))
        self._file_label.grid(row=0, column=0, sticky="ew")
        self._metadata_label = ctk.CTkLabel(info, text="", anchor="w", justify="left")
        self._metadata_label.grid(row=1, column=0, pady=(6, 0), sticky="ew")
        # Lista desplazable de nombres: ocupa el lugar de los metadatos cuando hay un lote.
        self._files_box = ctk.CTkTextbox(info, height=64, wrap="none", state="disabled")
        self._files_box.grid(row=1, column=0, pady=(6, 0), sticky="ew")
        self._files_box.grid_remove()

        # Solo video: perfil de calidad (resolución final + CRF), distinto del porcentaje de reducción.
        gray = ("gray30", "gray70")
        self._video_frame = ctk.CTkFrame(info, fg_color="transparent")
        self._video_frame.grid(row=2, column=0, pady=(8, 0), sticky="ew")
        self._video_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self._video_frame, anchor="w", justify="left", wraplength=480, text_color=gray,
            text="Resolución y calidad de compresión (CRF) son cosas distintas: un video puede pesar "
                 "mucho menos sin reducir su resolución.",
        ).grid(row=0, column=0, sticky="ew")
        profile_row = ctk.CTkFrame(self._video_frame, fg_color="transparent")
        profile_row.grid(row=1, column=0, pady=(6, 0), sticky="w")
        ctk.CTkLabel(profile_row, text="Perfil de calidad").pack(side="left", padx=(0, 8))
        self._profile_menu = ctk.CTkOptionMenu(
            profile_row, values=[p.label for p in PROFILES.values()], variable=self._profile_var,
            command=lambda _value: self._on_profile_changed(), width=190,
        )
        self._profile_menu.pack(side="left")
        self._profile_desc = ctk.CTkLabel(self._video_frame, text="", anchor="w", justify="left", wraplength=480,
                                          text_color=gray)
        self._profile_desc.grid(row=2, column=0, pady=(4, 0), sticky="ew")
        self._profile_warning = ctk.CTkLabel(self._video_frame, text="", anchor="w", justify="left",
                                             wraplength=480, text_color=WARN_COLOR)
        self._profile_warning.grid(row=3, column=0, sticky="ew")
        self._custom_frame = ctk.CTkFrame(self._video_frame, fg_color="transparent")
        self._custom_frame.grid(row=4, column=0, pady=(4, 0), sticky="w")
        ctk.CTkLabel(self._custom_frame, text="CRF (calidad H.264)").pack(side="left", padx=(0, 8))
        self._crf_menu = ctk.CTkOptionMenu(
            self._custom_frame, values=_crf_labels(), variable=self._crf_var,
            command=lambda _value: self._on_profile_changed(), width=230,
        )
        self._crf_menu.pack(side="left")
        self._custom_frame.grid_remove()

        self._question_label = ctk.CTkLabel(info, text="¿Qué porcentaje deseas reducir?", anchor="w")
        self._question_label.grid(row=3, column=0, pady=(8, 2), sticky="ew")
        percent_row = ctk.CTkFrame(info, fg_color="transparent")
        self._percent_row = percent_row
        percent_row.grid(row=4, column=0, sticky="w")
        self._percent_entry = ctk.CTkEntry(percent_row, width=80, textvariable=self._percent_var, justify="center")
        self._percent_entry.pack(side="left")
        ctk.CTkLabel(percent_row, text="%").pack(side="left", padx=(4, 12))
        self._preset_buttons = []
        for preset in (25, 50, 75):
            button = ctk.CTkButton(
                percent_row, text=f"{preset} %", width=60, fg_color=("gray70", "gray30"),
                text_color=("gray10", "gray90"), command=lambda value=preset: self._percent_var.set(str(value)),
            )
            button.pack(side="left", padx=3)
            self._preset_buttons.append(button)

        self._summary_label = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480)
        self._summary_label.grid(row=5, column=0, pady=(6, 0), sticky="ew")

        ctk.CTkLabel(info, text="Formato de salida", anchor="w").grid(row=6, column=0, pady=(8, 2), sticky="ew")
        self._format_selector = ctk.CTkSegmentedButton(
            info, values=["-"], command=lambda _value: self._update_format_hint()
        )
        self._format_selector.grid(row=7, column=0, sticky="w")
        self._format_hint = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480,
                                         text_color=("gray30", "gray70"))
        self._format_hint.grid(row=8, column=0, pady=(4, 0), sticky="ew")

        self._notice_label = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480,
                                          text_color=WARN_COLOR)
        self._notice_label.grid(row=9, column=0, pady=(6, 0), sticky="ew")

        self._convert_button = ctk.CTkButton(
            self._panel, text="CONVERTIR", height=48, font=ctk.CTkFont(size=16, weight="bold"),
            command=self._on_convert,
        )
        self._convert_button.grid(row=1, column=1, padx=(0, 16), pady=(8, 16), sticky="sew")

    # ------------------------------------------------------------- loading --
    def _on_load_video(self) -> None:
        self._choose_file(
            VIDEO_KIND,
            "Seleccionar video",
            [("Videos", _pattern(VIDEO_INPUT_EXTENSIONS)), ("Todos los archivos", "*.*")],
        )

    def _on_load_image(self) -> None:
        if self._busy:
            return
        selected = filedialog.askopenfilenames(
            title="Seleccionar una o varias imágenes",
            filetypes=[("Imágenes", _pattern(IMAGE_INPUT_EXTENSIONS)), ("Todos los archivos", "*.*")],
        )
        paths = [Path(item) for item in self.tk.splitlist(selected)]
        if len(paths) == 1:
            self.load_file(paths[0], IMAGE_KIND)
        elif paths:
            self.load_batch(paths)

    def _choose_file(self, kind: str, title: str, filetypes: list[tuple[str, str]]) -> None:
        if self._busy:
            return
        selected = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if selected:
            self.load_file(Path(selected), kind)

    def load_file(self, path: Path, kind: str) -> bool:
        """Valida el archivo y muestra el panel de conversión. Devuelve ``True`` si se cargó."""
        try:
            if kind == IMAGE_KIND:
                image = load_image(path)
                size, preview, formats = image_size(image), to_rgb_preview(image), list(available_image_formats())
                metadata = ""  # la resolución original ya aparece en el resumen
            else:
                formats = list(available_video_formats()) or list(VIDEO_PROFILES)  # sin FFmpeg: se muestra, pero se bloquea
                info = probe_video(path)
                size, preview, metadata = (info.width, info.height), to_rgb_preview(info.first_frame), self._video_metadata(info)
        except ConversionError as exc:
            self._show_error(f"No se pudo cargar el archivo «{path.name}».\n{exc}")
            return False
        except Exception:
            logger.exception("Error inesperado cargando %s", path)
            self._show_error(f"No se pudo cargar el archivo «{path.name}». Puede estar dañado.")
            return False

        if not formats:
            self._show_error("Esta instalación de OpenCV no puede codificar imágenes en ningún formato compatible.")
            return False

        self._source_path, self._kind, self._media_size = path, kind, size
        self._batch_paths = []
        self._file_label.configure(text=f"Archivo seleccionado: {path.name}")
        self._set_preview(preview)
        self._preview_caption.configure(text="")
        self._files_box.grid_remove()
        self._metadata_label.grid()
        self._configure_formats(formats, "MP4" if kind == VIDEO_KIND else self._preferred_format(path))
        self._notice_label.configure(text="")
        if kind == VIDEO_KIND:
            self._show_video_notice(info.has_audio)
        self._metadata_label.configure(text=metadata)
        self._show_panel()
        self._set_result_actions()
        self._reset_progress()
        self._video_has_audio = info.has_audio if kind == VIDEO_KIND else None
        self._apply_mode()
        self._refresh_summary()
        self._log(f"Archivo cargado ({kind}): {path.name} ({size[0]} × {size[1]} px)")
        return True

    def load_batch(self, paths: list[Path]) -> bool:
        """Carga varias imágenes como un lote. Solo se decodifica la primera legible (vista previa)."""
        formats = list(available_image_formats())
        if not formats:
            self._show_error("Esta instalación de OpenCV no puede codificar imágenes en ningún formato compatible.")
            return False

        previewed: tuple[Path, tuple[int, int]] | None = None
        for path in paths:
            try:
                image = load_image(path)
            except ConversionError:
                continue  # se informará como fallida al convertir; probamos la siguiente para la vista previa
            except Exception:
                logger.exception("Error inesperado cargando %s", path)
                continue
            self._set_preview(to_rgb_preview(image))
            previewed = (path, image_size(image))
            break
        if previewed is None:
            self._show_error("Ninguna de las imágenes seleccionadas se pudo abrir. Pueden estar dañadas o no ser compatibles.")
            return False

        preview_path, size = previewed
        self._source_path, self._kind, self._media_size = None, IMAGE_KIND, size
        self._batch_paths = list(paths)
        self._preview_path = preview_path
        self._file_label.configure(text=f"{len(paths)} imágenes seleccionadas")
        self._preview_caption.configure(text=f"Vista previa del lote: {preview_path.name}")
        self._metadata_label.grid_remove()
        self._files_box.grid()
        self._files_box.configure(state="normal")
        self._files_box.delete("1.0", "end")
        self._files_box.insert("end", "\n".join(path.name for path in paths))
        self._files_box.configure(state="disabled")
        self._configure_formats(formats, self._preferred_format(paths[0]))
        self._notice_label.configure(text="")
        self._show_panel()
        self._set_result_actions()
        self._reset_progress()
        self._video_has_audio = None
        self._apply_mode()
        self._refresh_summary()
        self._log(f"Lote cargado: {len(paths)} imágenes (primera: {paths[0].name})")
        return True

    def _show_panel(self) -> None:
        """Muestra el panel de conversión; la zona de resultados absorbe el espacio sobrante."""
        self._empty_label.grid_remove()
        self._panel.grid()
        self.grid_rowconfigure(2, weight=0)

    @staticmethod
    def _preferred_format(path: Path) -> str:
        extension = path.suffix.lstrip(".").upper()
        return {"JPEG": "JPG", "TIF": "TIFF"}.get(extension, extension)

    @staticmethod
    def _video_metadata(info: VideoInfo) -> str:
        frames = str(info.frame_count) if info.frame_count else "desconocido"
        return f"FPS: {info.fps:g}     Frames: {frames}     Duración: {format_duration(info.duration_seconds)}"

    def _show_video_notice(self, has_audio: bool | None) -> None:
        """Estado de FFmpeg y del audio del video cargado (verde si todo está listo, rojo si bloquea)."""
        if self._ffmpeg_info is None:
            self._notice_label.configure(text=self._ffmpeg_error or "", text_color=ERROR_COLOR)
        elif has_audio is False:
            self._notice_label.configure(text=FFMPEG_READY_NO_AUDIO_MESSAGE, text_color=OK_COLOR)
        else:
            self._notice_label.configure(text=FFMPEG_READY_MESSAGE, text_color=OK_COLOR)

    def _configure_formats(self, formats: list[str], preferred: str) -> None:
        self._format_selector.configure(values=formats)
        self._format_selector.set(preferred if preferred in formats else formats[0])
        self._update_format_hint()

    def _update_format_hint(self) -> None:
        """Explica el formato elegido (solo video: no todos los contenedores son equivalentes)."""
        profile = VIDEO_PROFILES.get(self._format_selector.get())
        if self._kind == VIDEO_KIND and profile is not None:
            self._format_hint.configure(text=f"{profile.description}\n{AVI_NOTE}")
        else:
            self._format_hint.configure(text="")

    def _set_preview(self, rgb_array) -> None:
        pil_image = Image.fromarray(rgb_array)
        pil_image.thumbnail(PREVIEW_MAX_SIZE, Image.LANCZOS)  # conserva la relación de aspecto
        self._preview_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=pil_image.size)
        self._preview_label.configure(image=self._preview_image, text="")

    # ------------------------------------------------------------- summary --
    def _current_percentage(self) -> float | None:
        try:
            return parse_percentage(self._percent_var.get())
        except ConversionError:
            return None

    def _selected_profile_key(self) -> str:
        return PROFILE_BY_LABEL[self._profile_var.get()].key

    def _current_quality(self) -> VideoQuality:
        """Perfil de calidad de video elegido (lanza ``ConversionError`` si el modo personalizado es inválido)."""
        return make_quality(self._selected_profile_key(), self._percent_var.get(), self._crf_var.get())

    def _settings_valid(self) -> bool:
        if self._kind == VIDEO_KIND:
            try:
                self._current_quality()
            except ConversionError:
                return False
            return True
        return self._current_percentage() is not None

    def _apply_mode(self) -> None:
        """Muestra los controles de imagen o de video según lo cargado."""
        is_video = self._kind == VIDEO_KIND
        if self._percent_default_kind != self._kind:  # al cambiar de imagen a video (o al revés)
            self._percent_default_kind = self._kind
            self._percent_var.set("0" if is_video else "50")
        custom = is_video and self._selected_profile_key() == "custom"
        for widget, visible in ((self._video_frame, is_video), (self._custom_frame, custom),
                                (self._question_label, not is_video or custom),
                                (self._percent_row, not is_video or custom)):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        self._question_label.configure(
            text="Reducción de resolución (0 % = conservar la resolución original)"
            if is_video else "¿Qué porcentaje deseas reducir?"
        )
        self._update_profile_text()

    def _update_profile_text(self) -> None:
        info = PROFILE_BY_LABEL[self._profile_var.get()]
        warning = info.warning or ""
        if info.key == "custom":
            try:
                if parse_crf(self._crf_var.get()) > CRF_WARNING_ABOVE:
                    warning = CRF_HIGH_WARNING
            except ConversionError:
                pass
        self._profile_desc.configure(text=info.description)
        self._profile_warning.configure(text=warning)

    def _on_profile_changed(self) -> None:
        self._apply_mode()
        self._refresh_summary()

    def _refresh_video_summary(self, width: int, height: int) -> None:
        """Resolución y calidad de compresión por separado, como pares «campo: valor»."""
        try:
            quality = self._current_quality()
        except ConversionError as exc:
            self._summary_label.configure(text=str(exc), text_color=ERROR_COLOR)
            self._update_controls()
            return
        out_width, out_height = quality.target_size(width, height)
        audio = {True: "AAC — preservado", False: "El archivo original no contiene audio"}.get(
            self._video_has_audio, "—"
        )
        text = (
            f"Resolución original: {width} × {height} px\n"
            f"Perfil: {quality.label}\n"
            f"Resolución final: {out_width} × {out_height} px"
            f"{'' if (out_width, out_height) != (width, height) else '  (se conserva)'}\n"
            f"Calidad H.264: CRF {quality.crf}\n"
            f"Audio: {audio}"
        )
        self._summary_label.configure(text=text, text_color=("gray10", "gray90"))
        self._update_controls()

    def _refresh_summary(self) -> None:
        if self._media_size is None:
            return
        width, height = self._media_size
        if self._kind == VIDEO_KIND:
            self._refresh_video_summary(width, height)
            return
        try:
            percent = parse_percentage(self._percent_var.get())
        except ConversionError as exc:
            self._summary_label.configure(text=str(exc), text_color=ERROR_COLOR)
            self._update_controls()
            return

        out_width, out_height = calculate_output_size(width, height, percent)
        if self._batch_paths:
            text = (
                f"Reducción: {percent:g} %  (cada imagen conserva el {100 - percent:g} % de su ancho y alto)\n"
                "La resolución de salida se calcula por separado para cada imagen.\n"
                f"Ejemplo con «{self._preview_path.name}»: {width} × {height} → {out_width} × {out_height} px"
            )
            self._summary_label.configure(text=text, text_color=("gray10", "gray90"))
            self._update_controls()
            return
        text = (
            f"Resolución original: {width} × {height} px\n"
            f"Reducción: {percent:g} %  (cada lado queda en {100 - percent:g} % de su tamaño original)\n"
            f"Resolución de salida: {out_width} × {out_height} px"
        )
        self._summary_label.configure(text=text, text_color=("gray10", "gray90"))
        self._update_controls()

    # --------------------------------------------------------- conversion --
    def _on_convert(self) -> None:
        sources = self._batch_paths or ([self._source_path] if self._source_path else [])
        if self._busy or not sources or self._kind is None:
            return
        if self._kind == VIDEO_KIND and self._ffmpeg_info is None:
            self._show_error(self._ffmpeg_error or "FFmpeg no está disponible.")
            return
        percent: float | None = None
        quality: VideoQuality | None = None
        if self._kind == VIDEO_KIND:
            try:
                quality = self._current_quality()
            except ConversionError as exc:
                self._show_error(str(exc))
                return
        else:
            percent = self._current_percentage()
            if percent is None:
                self._show_error("Ingresa un porcentaje válido entre 1 y 99 antes de convertir.")
                return
        format_label = self._format_selector.get()
        kind = self._kind

        self._cancel_event = threading.Event()
        self._set_busy(True)
        self._set_result_actions()
        self._reset_progress()
        idle_color = ("gray10", "gray90")
        if kind == VIDEO_KIND:
            self._status_label.configure(text="Preparando video...", text_color=idle_color)
            self._start_indeterminate()
        elif len(sources) > 1:
            self._status_label.configure(text=f"Procesando imagen 1 de {len(sources)} (0%)", text_color=idle_color)
        else:
            self._status_label.configure(text="Procesando imagen...", text_color=idle_color)
            self._start_indeterminate()
        what = f"{len(sources)} imágenes" if len(sources) > 1 else sources[0].name
        settings = f"perfil {quality.label} ({quality.quality_text})" if quality else f"reducción {percent:g} %"
        self._log(f"Iniciando conversión de {what} → {format_label}, {settings}")

        self._worker = threading.Thread(
            target=self._run_worker, args=(sources, kind, percent, quality, format_label, self._cancel_event),
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, sources: list[Path], kind: str, percent: float | None, quality: VideoQuality | None,
                    format_label: str, cancel_event: threading.Event) -> None:
        """Se ejecuta en un hilo: solo comunica con la GUI mediante la cola de eventos."""
        source = sources[0]
        try:
            if kind == IMAGE_KIND:
                batch = convert_images(
                    sources, percent, format_label,
                    progress_callback=lambda done, total, name: self._events.put(("batch_progress", done, total, name)),
                    cancel_event=cancel_event,
                )
                self._events.put(("batch_done", batch))
            else:
                result = convert_video(
                    source, format_label, quality,
                    progress_callback=lambda stage, done, total: self._events.put(("progress", stage, done, total)),
                    cancel_event=cancel_event,
                )
                self._events.put(("done", result))
        except ConversionCancelled:
            self._events.put(("cancelled",))
        except ConversionError as exc:
            logger.warning("Conversión fallida: %s", exc, exc_info=True)
            self._events.put(("error", str(exc)))
        except Exception:
            logger.exception("Error inesperado durante la conversión de %s", source)
            self._events.put(("error", "Ocurrió un error inesperado durante la conversión. Revisa logs/app.log."))

    def _poll_events(self) -> None:
        """Corre en el hilo de la GUI y aplica los eventos enviados por el worker."""
        try:
            while True:
                event = self._events.get_nowait()
                handler = {
                    "progress": self._handle_progress,
                    "batch_progress": self._handle_batch_progress,
                    "batch_done": self._handle_batch_done,
                    "done": self._handle_done,
                    "error": self._handle_error,
                    "cancelled": self._handle_cancelled,
                }[event[0]]
                handler(*event[1:])
        except queue.Empty:
            pass
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll_events)

    def _handle_progress(self, stage: str, done: int, total: int | None) -> None:
        """Etapas del video: preparar → codificar (FFmpeg) → validar."""
        if not self._busy:
            return
        if stage == STAGE_ENCODING and total:
            if self._indeterminate:
                self._stop_indeterminate()
            fraction = min(done / total, 1.0)
            self._progress.set(fraction)
            self._status_label.configure(text=f"Codificando H.264 y audio... {int(fraction * 100)}%")
            return
        messages = {
            STAGE_PREPARING: "Preparando video...",
            STAGE_ENCODING: f"Codificando H.264 y audio... {done} frames",  # total desconocido
            STAGE_VALIDATING: "Validando archivo final...",
        }
        if not self._indeterminate:
            self._start_indeterminate()
        self._status_label.configure(text=messages.get(stage, "Procesando video..."))

    def _handle_batch_progress(self, done: int, total: int, current_name: str | None) -> None:
        if not self._busy or total < 2 or current_name is None:
            return
        fraction = done / total
        self._progress.set(fraction)
        self._status_label.configure(
            text=f"Procesando imagen {done + 1} de {total} ({int(fraction * 100)}%) — Procesando: {current_name}"
        )

    def _handle_batch_done(self, batch: BatchResult) -> None:
        if not batch.used_batch_folder:  # una sola imagen: mismo comportamiento de siempre
            if batch.succeeded:
                self._handle_done(batch.succeeded[0])
            else:
                self._handle_error(batch.failed[0][1])
            return

        self._set_busy(False)
        self._progress.set(batch.processed / batch.total)
        folder_exists = batch.output_dir.is_dir()
        self._output_folder = batch.output_dir if folder_exists else OUTPUT_DIR
        self._failed_files = batch.failed
        ok_count, failed_count = len(batch.succeeded), len(batch.failed)

        title = "Conversión cancelada" if batch.cancelled else "Conversión finalizada"
        lines = [title, "", f"Correctas: {ok_count}", f"Fallidas: {failed_count}"]
        if batch.cancelled:
            lines.append(f"Sin procesar: {batch.total - batch.processed}")
        if folder_exists:
            lines += ["", "Carpeta:", f"{display_path(batch.output_dir)}/"]
        if batch.failed:
            lines += ["", "Archivos con error:"]
            lines += [f"  • {path.name}: {reason}" for path, reason in batch.failed]
        notes = Counter(warning for result in batch.succeeded for warning in result.warnings)
        lines += [f"Aviso: {text} ({count} imágenes)" for text, count in notes.items()]

        if batch.cancelled:
            color, status = WARN_COLOR, f"Conversión cancelada: {ok_count} de {batch.total} imágenes convertidas."
        elif ok_count == 0:
            color, status = ERROR_COLOR, "La conversión falló: ninguna imagen se pudo convertir."
        elif failed_count:
            color, status = WARN_COLOR, (
                f"Conversión finalizada: {ok_count} correcta{'s' if ok_count != 1 else ''}, "
                f"{failed_count} fallida{'s' if failed_count != 1 else ''}."
            )
        else:
            color, status = OK_COLOR, f"Conversión finalizada: {ok_count} de {batch.total} imágenes convertidas."
        self._status_label.configure(text=status, text_color=color)
        self._log("\n".join(lines), color)
        self._set_result_actions(open_folder=folder_exists, failed=bool(batch.failed))
        if ok_count == 0 and not batch.cancelled:
            messagebox.showerror(
                "Conversor Multimedia",
                "Ninguna imagen se pudo convertir. Usa «Ver archivos fallidos» para ver el motivo.",
                parent=self,
            )

    def _handle_done(self, result: ConversionResult) -> None:
        self._stop_indeterminate()
        self._progress.set(1)
        self._set_busy(False)
        self._output_folder = result.output_path.parent
        self._status_label.configure(text="Conversión completada correctamente.", text_color=OK_COLOR)
        out_w, out_h = result.output_size
        if result.video_codec is not None:  # video: datos verificados sobre el archivo final
            src_w, src_h = result.source_size or result.output_size
            audio_line = (
                f"Audio: {result.audio_codec} — preservado"
                if result.has_source_audio
                else "Audio: El archivo original no contiene audio"
            )
            lines = [
                "Conversión completada correctamente.",
                f"Resolución: {src_w}×{src_h} → {out_w}×{out_h}",
                f"Codec: {result.video_codec}",
                f"Calidad: {result.quality_label}",
                audio_line,
            ]
            out_bytes = result.output_path.stat().st_size
            if result.source_bytes:
                lines.append(f"Tamaño: {format_bytes(result.source_bytes)} → {format_bytes(out_bytes)}")
                change = (1 - out_bytes / result.source_bytes) * 100
                lines.append(
                    f"Reducción: {change:.1f}%" if change >= 0
                    else f"Aumento: {-change:.1f}% (el original ya estaba muy comprimido)"
                )
            lines += [
                f"Formato: {result.format_label}",
                f"Archivo: {display_path(result.output_path)}",
                f"Ruta completa: {result.output_path}",
            ]
        else:
            lines = [
                "Conversión completada correctamente.",
                f"Archivo de salida: {display_path(result.output_path)}",
                f"Ruta completa: {result.output_path}",
                f"Resolución de salida: {out_w} × {out_h} px",
            ]
        lines += [f"Aviso: {warning}" for warning in result.warnings]
        self._log("\n".join(lines), OK_COLOR)
        self._set_result_actions(open_folder=True)

    def _handle_error(self, message: str) -> None:
        self._stop_indeterminate()
        self._progress.set(0)
        self._set_busy(False)
        self._status_label.configure(text="La conversión falló.", text_color=ERROR_COLOR)
        self._show_error(message)

    def _handle_cancelled(self) -> None:
        self._stop_indeterminate()
        self._progress.set(0)
        self._set_busy(False)
        self._status_label.configure(text="Conversión cancelada.", text_color=WARN_COLOR)
        self._log("Conversión cancelada. No se guardó ningún archivo.", WARN_COLOR)

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self._cancel_button.configure(state="disabled")
        self._status_label.configure(text="Cancelando...")

    # ----------------------------------------------------- state helpers --
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy and (self._kind == VIDEO_KIND or self._batch_paths):
            self._cancel_button.configure(state="normal")
            self._cancel_button.grid()
        else:
            self._cancel_button.grid_remove()
        self._update_controls()

    def _update_controls(self) -> None:
        idle = "disabled" if self._busy else "normal"
        for widget in (self._video_button, self._image_button, self._percent_entry, self._profile_menu,
                       self._crf_menu, self._format_selector, *self._preset_buttons):
            widget.configure(state=idle)
        has_selection = self._source_path is not None or bool(self._batch_paths)
        video_blocked = self._kind == VIDEO_KIND and self._ffmpeg_info is None  # sin FFmpeg no hay video
        valid = self._settings_valid() and has_selection and not video_blocked
        self._convert_button.configure(state="normal" if valid and not self._busy else "disabled")

    def _set_result_actions(self, open_folder: bool = False, failed: bool = False) -> None:
        """Muestra u oculta los botones que aparecen bajo el área de resultados."""
        self._open_button.pack_forget()
        self._failed_button.pack_forget()
        if open_folder:
            self._open_button.pack(side="right")
        if failed:
            self._failed_button.pack(side="right", padx=(0, 8))

    def _show_failed_files(self) -> None:
        """Ventana con la lista completa de archivos que no se pudieron convertir y su motivo."""
        if not self._failed_files:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Archivos fallidos")
        dialog.geometry("680x360")
        dialog.transient(self)
        box = ctk.CTkTextbox(dialog, wrap="word")
        box.pack(fill="both", expand=True, padx=12, pady=12)
        for path, reason in self._failed_files:
            box.insert("end", f"{path}\n    {reason}\n\n")
        box.configure(state="disabled")
        dialog.after(150, dialog.focus)

    def _reset_progress(self) -> None:
        self._stop_indeterminate()
        self._progress.set(0)
        self._status_label.configure(text="", text_color=("gray10", "gray90"))

    def _start_indeterminate(self) -> None:
        self._indeterminate = True
        self._progress.configure(mode="indeterminate")
        self._progress.start()

    def _stop_indeterminate(self) -> None:
        if self._indeterminate:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._indeterminate = False

    def _log(self, message: str, color: tuple[str, str] | None = None) -> None:
        self._results_box.configure(state="normal")
        message_start = self._results_box.index("end-1c")
        if color is None:
            self._results_box.insert("end", message + "\n\n")
        else:
            tag = f"color_{color[0]}"
            light, dark = color  # tag_config no resuelve pares claro/oscuro por sí solo
            self._results_box.tag_config(tag, foreground=light if ctk.get_appearance_mode() == "Light" else dark)
            self._results_box.insert("end", message + "\n\n", tag)
        self._results_box.see("end")
        self._results_box.see(message_start)  # que se vea el principio del último mensaje, no solo su final
        self._results_box.configure(state="disabled")

    def _show_error(self, message: str) -> None:
        self._log(f"Error: {message}", ERROR_COLOR)
        messagebox.showerror("Conversor Multimedia", message, parent=self)

    def _open_output_folder(self) -> None:
        folder = self._output_folder if self._output_folder.is_dir() else ensure_output_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            logger.exception("No se pudo abrir %s", folder)
            self._show_error(f"No se pudo abrir la carpeta automáticamente. Búscala en: {OUTPUT_DIR}")

    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askyesno(
                "Conversor Multimedia", "Hay una conversión en curso. ¿Cancelarla y salir?", parent=self
            ):
                return
            self._cancel_event.set()
            if self._worker is not None:
                self._worker.join(timeout=10)
        self.after_cancel(self._poll_job)
        self.destroy()
