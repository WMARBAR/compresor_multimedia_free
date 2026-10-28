"""Ventana principal (CustomTkinter): orquesta paneles y servicios; no contiene lógica de OpenCV ni de FFmpeg.

Los trabajos pesados (inspección, conversión, miniaturas) corren en hilos y solo se comunican
con la interfaz mediante ``self._events``. El sondeo de esa cola solo está activo mientras hay
trabajo en curso, así que en reposo la aplicación no consume CPU.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from services.common import (
    OUTPUT_DIR,
    BatchResult,
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    display_path,
    ensure_output_dir,
    format_bytes,
    format_clock,
    format_elapsed,
)
from services.compression_stats import format_reduction, larger_warning
from services.ffmpeg_service import VIDEO_PROFILES, ffmpeg_status
from services.history import HistoryEntry, SessionHistory
from services.image_processor import (
    IMAGE_INPUT_EXTENSIONS,
    available_image_formats,
    convert_images,
    load_thumbnail,
)
from services.metadata_service import (
    IMAGE_KIND,
    VIDEO_KIND,
    LoadPlan,
    MediaMetadata,
    inspect_image,
    inspect_media,
    plan_load,
)
from services.video_processor import (
    STAGE_ENCODING,
    STAGE_PREPARING,
    STAGE_VALIDATING,
    VIDEO_INPUT_EXTENSIONS,
    available_video_formats,
    convert_video,
)

from . import theme
from .config_panel import ConfigPanel, ConversionSettings
from .dnd import FileDropTarget
from .dropzone import DropZone
from .history_panel import HistoryPanel
from .result_panel import COMPARE_BOX, ResultActions, ResultPanel
from .source_panel import SourcePanel
from .widgets import Card, LogView, ToolTip, label, primary_button, set_primary_enabled

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 100
PROGRESS_MILESTONES = (25, 50, 75)  # el registro no vuelca cada porcentaje, solo hitos


def _pattern(extensions: tuple[str, ...]) -> str:
    return " ".join(f"*{ext}" for ext in extensions)


def _names(extensions: tuple[str, ...]) -> str:
    return " ".join(ext.lstrip(".").upper() for ext in extensions)


class MainWindow(ctk.CTk):
    """Panel principal: carga archivos, configura la salida, convierte y muestra resultados."""

    def __init__(self) -> None:
        theme.apply_theme()
        super().__init__(fg_color=theme.BG)
        self.title("Media Compressor — Conversor Multimedia")
        height = min(900, self.winfo_screenheight() - 90)  # cabe también en pantallas de 768 px
        self.geometry(f"1180x{height}")
        self.minsize(900, min(560, height))

        self._meta: MediaMetadata | None = None  # archivo único cargado (o vista previa del lote)
        self._source_paths: list[Path] = []
        self._batch_total_bytes = 0
        self._kind: str | None = None
        self._busy = False
        self._inspecting = False
        self._compare_pending = False
        self._load_token = 0
        self._compare_token = 0
        self._failed_files: list[tuple[Path, str]] = []
        self._last_batch: BatchResult | None = None
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._events: queue.Queue[tuple] = queue.Queue()
        self._poll_job: str | None = None
        self._indeterminate = False
        self._started_at = 0.0
        self._logged_milestones: set[int] = set()
        self._logged_stages: set[str] = set()
        self._history = SessionHistory()
        self._jobs: list[str] = []  # temporizadores pendientes (se cancelan al cerrar)

        # FFmpeg se verifica una sola vez al arrancar; sin él no se convierten videos.
        self._ffmpeg_info, self._ffmpeg_error = ffmpeg_status()

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _e: self._pick_any())
        self.bind("<Control-Return>", lambda _e: self._on_convert())
        self.bind("<Escape>", lambda _e: self._on_cancel() if self._busy else None)

        self._drop = FileDropTarget(self, self._on_files_dropped)
        self._drop_active = False
        self._jobs.append(self.after(250, self._install_drop))
        self._startup_log()

    # ------------------------------------------------------------------ layout --
    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()

        self._body = ctk.CTkScrollableFrame(
            self, fg_color=theme.BG, corner_radius=0, scrollbar_button_color=theme.BORDER_STRONG,
            scrollbar_button_hover_color=theme.ACCENT_DIM,
        )
        self._body.grid(row=1, column=0, sticky="nsew")
        self._body.grid_columnconfigure(0, weight=1)
        body = self._body

        self._dropzone = DropZone(
            body, _names(VIDEO_INPUT_EXTENSIONS), _names(IMAGE_INPUT_EXTENSIONS),
            self._pick_video, self._pick_images, self._pick_any, FileDropTarget.supported(),
        )
        self._dropzone.grid(row=0, column=0, padx=theme.PAD, pady=(theme.GAP, theme.GAP), sticky="ew")

        columns = ctk.CTkFrame(body, fg_color="transparent")
        columns.grid(row=1, column=0, padx=theme.PAD, sticky="ew")
        columns.grid_columnconfigure((0, 1), weight=1, uniform="cards")
        self._source_panel = SourcePanel(columns)
        self._source_panel.grid(row=0, column=0, padx=(0, theme.GAP // 2), sticky="nsew")
        self._config_panel = ConfigPanel(columns, self._update_controls)
        self._config_panel.grid(row=0, column=1, padx=(theme.GAP // 2, 0), sticky="nsew")
        self._config_panel.set_ffmpeg_error(self._ffmpeg_error)
        self._config_panel.clear()

        self._convert_button = primary_button(
            body, "▶  INICIAR CONVERSIÓN", self._on_convert, height=52, font=theme.ui_font(16, "bold"),
        )
        self._convert_button.grid(row=2, column=0, padx=theme.PAD, pady=theme.GAP, sticky="ew")
        set_primary_enabled(self._convert_button, False)

        self._build_progress(body)
        self._result_panel = ResultPanel(body, ResultActions(
            open_file=self._open_path, open_folder=self._open_path, copy_path=self._copy_path,
            discard=self._discard_result, show_failed=self._show_failed_files, select_compare=self._select_compare,
        ))
        self._result_panel.grid(row=4, column=0, padx=theme.PAD, pady=(0, theme.GAP), sticky="ew")
        self._result_panel.grid_remove()

        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=5, column=0, padx=theme.PAD, pady=(0, theme.PAD), sticky="ew")
        bottom.grid_columnconfigure((0, 1), weight=1, uniform="bottom")
        self._history_panel = HistoryPanel(bottom, self._open_path)
        self._history_panel.grid(row=0, column=0, padx=(0, theme.GAP // 2), sticky="nsew")
        log_card = Card(bottom, "SYSTEM LOG")
        self._log_view = LogView(log_card, height=150)
        log_card.add(self._log_view, pady=(0, theme.INNER))
        log_card.grid(row=0, column=1, padx=(theme.GAP // 2, 0), sticky="nsew")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0, border_width=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, padx=theme.PAD, pady=(10, 10), sticky="w")
        label(titles, "> MEDIA_COMPRESSOR", size=20, weight="bold", mono=True, color=theme.ACCENT).pack(anchor="w")
        label(titles, "LOCAL MULTIMEDIA OPTIMIZATION SYSTEM  ·  100% LOCAL · SIN SUBIDAS A LA NUBE", size=10,
              mono=True, color=theme.MUTED).pack(anchor="w")
        online = self._ffmpeg_info is not None
        self._ffmpeg_pill = label(
            header, "● FFMPEG ONLINE" if online else "✗ FFMPEG OFFLINE", size=12, weight="bold", mono=True,
            color=theme.ACCENT if online else theme.ERROR, anchor="e",
        )
        self._ffmpeg_pill.grid(row=0, column=1, padx=theme.PAD, sticky="e")
        tip = (f"FFmpeg {self._ffmpeg_info.version} ({self._ffmpeg_info.origin})\n{self._ffmpeg_info.path}"
               if online else self._ffmpeg_error or "FFmpeg no disponible")
        self._ffmpeg_tip = ToolTip(self._ffmpeg_pill, tip)
        ctk.CTkFrame(self, height=1, fg_color=theme.BORDER, corner_radius=0).grid(row=0, column=0, sticky="sew")

    def _build_progress(self, body) -> None:
        card = Card(body, "PROCESSING")
        card.grid(row=3, column=0, padx=theme.PAD, pady=(0, theme.GAP), sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid_columnconfigure(0, weight=1)
        self._status_label = label(top, "En espera.", size=12, mono=True, color=theme.MUTED, wraplength=760)
        self._status_label.grid(row=0, column=0, sticky="w")
        self._percent_label = label(top, "", size=14, weight="bold", mono=True, color=theme.ACCENT, anchor="e")
        self._percent_label.grid(row=0, column=1, sticky="e")
        card.add(top, pady=(0, 4))
        self._progress = ctk.CTkProgressBar(
            card, height=12, corner_radius=4, fg_color=theme.CARD_ALT, progress_color=theme.ACCENT,
            border_width=1, border_color=theme.BORDER, indeterminate_speed=1.0,
        )
        self._progress.set(0)
        card.add(self._progress, pady=(0, 6))
        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.grid_columnconfigure(0, weight=1)
        self._elapsed_label = label(bottom, "", size=11, mono=True, color=theme.MUTED)
        self._elapsed_label.grid(row=0, column=0, sticky="w")
        self._cancel_button = ctk.CTkButton(
            bottom, text="CANCELAR", width=120, height=30, font=theme.ui_font(12, "bold"),
            command=self._on_cancel, **theme.DANGER_BUTTON,
        )
        self._cancel_button.grid(row=0, column=1, sticky="e")
        self._cancel_button.grid_remove()
        card.add(bottom, pady=(0, theme.INNER))
        self._progress_card = card

    def _startup_log(self) -> None:
        if self._ffmpeg_info is not None:
            self._log(f"FFmpeg {self._ffmpeg_info.version} detectado ({self._ffmpeg_info.origin})")
        else:
            self._log(self._ffmpeg_error or "FFmpeg no disponible", "error")
        try:
            ensure_output_dir()
        except ConversionError as exc:
            self._log(str(exc), "error")
        self._log(f"Salida: {display_path(OUTPUT_DIR)}/")

    def _install_drop(self) -> None:
        self._drop_active = self._drop.install()
        if self._drop_active:
            self._log("Arrastrar y soltar activo")
        elif FileDropTarget.supported():
            self._log("Arrastrar y soltar no disponible: usa los botones para elegir archivos", "warn")

    # ------------------------------------------------------------------ polling --
    def _ensure_polling(self) -> None:
        if self._poll_job is None:
            self._poll_job = self.after(POLL_INTERVAL_MS, self._poll_events)

    def _poll_events(self) -> None:
        """Corre en el hilo de la GUI y aplica los eventos de los hilos; se detiene sola en reposo."""
        self._poll_job = None
        handlers = {
            "progress": self._handle_progress,
            "batch_progress": self._handle_batch_progress,
            "batch_done": self._handle_batch_done,
            "done": self._handle_done,
            "error": self._handle_error,
            "cancelled": self._handle_cancelled,
            "inspected": self._handle_inspected,
            "inspect_error": self._handle_inspect_error,
            "compare": self._handle_compare,
        }
        try:
            while True:
                event = self._events.get_nowait()
                handlers[event[0]](*event[1:])
        except queue.Empty:
            pass
        if self._busy:
            text = f"Tiempo: {format_clock(time.monotonic() - self._started_at)}"
            if self._elapsed_label.cget("text") != text:
                self._elapsed_label.configure(text=text)
        if self._busy or self._inspecting or self._compare_pending or not self._events.empty():
            self._ensure_polling()

    # ------------------------------------------------------------------ loading --
    def _pick_video(self) -> None:
        if self._busy or self._inspecting:
            return
        selected = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[("Videos", _pattern(VIDEO_INPUT_EXTENSIONS)), ("Todos los archivos", "*.*")],
        )
        if selected:
            self._load_paths([Path(selected)])

    def _pick_images(self) -> None:
        if self._busy or self._inspecting:
            return
        selected = filedialog.askopenfilenames(
            title="Seleccionar una o varias imágenes",
            filetypes=[("Imágenes", _pattern(IMAGE_INPUT_EXTENSIONS)), ("Todos los archivos", "*.*")],
        )
        paths = [Path(item) for item in self.tk.splitlist(selected)]
        if paths:
            self._load_paths(paths)

    def _pick_any(self) -> None:
        if self._busy or self._inspecting:
            return
        selected = filedialog.askopenfilenames(
            title="Seleccionar video o imágenes",
            filetypes=[
                ("Videos e imágenes", f"{_pattern(VIDEO_INPUT_EXTENSIONS)} {_pattern(IMAGE_INPUT_EXTENSIONS)}"),
                ("Todos los archivos", "*.*"),
            ],
        )
        paths = [Path(item) for item in self.tk.splitlist(selected)]
        if paths:
            self._load_paths(paths)

    def _on_files_dropped(self, paths: list[Path]) -> None:
        self._log(f"Soltados {len(paths)} archivo(s)")
        self._load_paths(paths)

    def load_file(self, path: Path, kind: str | None = None) -> bool:
        """Carga un archivo por ruta (atajo para pruebas y scripts). Devuelve ``True`` si se inició la carga."""
        return self._load_paths([path])

    def load_batch(self, paths: list[Path]) -> bool:
        return self._load_paths(list(paths))

    def _load_paths(self, paths: list[Path]) -> bool:
        if self._busy or self._inspecting:
            self._log("Hay una operación en curso: espera a que termine para cargar otro archivo", "warn")
            return False
        try:
            plan = plan_load(list(paths))
        except ConversionError as exc:
            self._show_error(str(exc))
            return False
        for warning in plan.warnings:
            self._log(warning, "warn")
        if plan.kind == IMAGE_KIND and not available_image_formats():
            self._show_error("Esta instalación de OpenCV no puede codificar imágenes en ningún formato compatible.")
            return False

        self._load_token += 1
        self._inspecting = True
        self._progress.set(0)
        self._percent_label.configure(text="")
        self._elapsed_label.configure(text="")
        self._update_controls()
        self._set_status(f"Analizando {plan.files[0].name}..." if len(plan.files) == 1
                         else f"Analizando {len(plan.files)} imágenes...", theme.MUTED)
        self._start_indeterminate()
        threading.Thread(target=self._inspect_worker, args=(self._load_token, plan), daemon=True).start()
        self._ensure_polling()
        return True

    def _inspect_worker(self, token: int, plan: LoadPlan) -> None:
        """Hilo: lee metadatos y la vista previa (o la primera imagen legible de un lote)."""
        try:
            if len(plan.files) == 1:
                self._events.put(("inspected", token, plan, inspect_media(plan.files[0], plan.kind), 0))
                return
            meta, total = None, 0
            for path in plan.files:
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
                if meta is None:
                    try:
                        meta = inspect_image(path)
                    except ConversionError:
                        continue  # se informará como fallida al convertir; probamos la siguiente
            if meta is None:
                raise ConversionError(
                    "Ninguna de las imágenes seleccionadas se pudo abrir. Pueden estar dañadas o no ser compatibles."
                )
            self._events.put(("inspected", token, plan, meta, total))
        except ConversionError as exc:
            self._events.put(("inspect_error", token, plan.files[0], str(exc)))
        except Exception:
            logger.exception("Error inesperado cargando %s", plan.files[0])
            self._events.put(("inspect_error", token, plan.files[0], "No se pudo cargar el archivo. Puede estar dañado."))

    def _handle_inspect_error(self, token: int, path: Path, message: str) -> None:
        if token != self._load_token:
            return
        self._inspecting = False
        self._stop_indeterminate()
        self._set_status("En espera.", theme.MUTED)
        self._update_controls()
        self._show_error(f"No se pudo cargar «{path.name}». {message}")

    def _handle_inspected(self, token: int, plan: LoadPlan, meta: MediaMetadata, total_bytes: int) -> None:
        if token != self._load_token:
            return
        self._inspecting = False
        self._stop_indeterminate()
        self._set_status("En espera.", theme.MUTED)
        is_video = plan.kind == VIDEO_KIND
        formats = list(available_video_formats()) or list(VIDEO_PROFILES) if is_video else list(available_image_formats())

        self._meta, self._kind, self._source_paths = meta, plan.kind, list(plan.files)
        self._batch_total_bytes = total_bytes
        self._result_panel.hide()
        self._failed_files, self._last_batch = [], None
        self._percent_label.configure(text="")
        self._elapsed_label.configure(text="")
        self._progress.set(0)
        batch = len(plan.files) > 1
        if batch:
            self._source_panel.show_batch(plan.files, total_bytes, meta)
        else:
            self._source_panel.show_single(meta)
        preferred = "MP4" if is_video else self._preferred_format(plan.files[0])
        self._config_panel.set_media(
            plan.kind, meta.size, formats, preferred,
            has_audio=meta.has_audio if is_video else None,
            has_alpha=None if batch or is_video else meta.has_alpha,
            example_name=meta.path.name if batch else None,
        )
        self._config_panel.set_ffmpeg_error(self._ffmpeg_error if is_video else None)
        self._update_controls()

        if batch:
            self._log(f"Lote cargado: {len(plan.files)} imágenes ({format_bytes(total_bytes)})")
        else:
            self._log(f"Archivo cargado: {meta.path.name}")
            if is_video:
                self._log(f"Stream de video: {meta.video_codec or 'códec no identificado'}")
                if meta.has_audio:
                    self._log(f"Stream de audio: {meta.audio_codec or 'presente'}")
                elif meta.has_audio is False:
                    self._log("Sin stream de audio (no es un error)", "warn")
            else:
                self._log(f"Imagen {meta.container or ''} {meta.width}×{meta.height}".replace("  ", " "))

    @staticmethod
    def _preferred_format(path: Path) -> str:
        extension = path.suffix.lstrip(".").upper()
        return {"JPEG": "JPG", "TIF": "TIFF"}.get(extension, extension)

    # --------------------------------------------------------------- conversion --
    def _on_convert(self) -> None:
        if self._busy or self._inspecting or self._kind is None or not self._source_paths:
            return
        settings, error = self._config_panel.evaluate()
        if settings is None:
            self._show_error(error or "Revisa la configuración de salida.")
            return
        sources, kind = list(self._source_paths), self._kind

        self._result_panel.hide()
        self._cancel_event = threading.Event()
        self._started_at = time.monotonic()
        self._logged_milestones.clear()
        self._logged_stages.clear()
        self._set_busy(True)
        self._progress.set(0)
        self._percent_label.configure(text="")
        self._elapsed_label.configure(text="Tiempo: 00:00:00")
        if kind == VIDEO_KIND:
            self._set_status("Preparando video...")
            self._start_indeterminate()
            quality = settings.quality
            self._log(f"Perfil: {quality.label.upper()} · CRF {quality.crf} · preset {quality.preset}")
        elif len(sources) > 1:
            self._set_status(f"Procesando 1 / {len(sources)}")
            self._log(f"Reducción: {settings.percent:g}% por imagen → {settings.format_label}")
        else:
            self._set_status("Procesando imagen...")
            self._start_indeterminate()
            self._log(f"Reducción: {settings.percent:g}% → {settings.format_label}")
        self._log("Conversión iniciada")

        self._worker = threading.Thread(
            target=self._run_worker, args=(sources, settings, self._cancel_event), daemon=True)
        self._worker.start()
        self._ensure_polling()
        self._scroll_to(self._progress_card)

    def _run_worker(self, sources: list[Path], settings: ConversionSettings, cancel_event: threading.Event) -> None:
        """Se ejecuta en un hilo: solo comunica con la GUI mediante la cola de eventos."""
        source = sources[0]
        try:
            if settings.kind == IMAGE_KIND:
                batch = convert_images(
                    sources, settings.percent, settings.format_label,
                    progress_callback=lambda done, total, name: self._events.put(("batch_progress", done, total, name)),
                    cancel_event=cancel_event, image_quality=settings.image_quality,
                )
                self._events.put(("batch_done", batch))
            else:
                result = convert_video(
                    source, settings.format_label, settings.quality,
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
            self._events.put(("error", "Ocurrió un error inesperado durante la conversión."))

    def _handle_progress(self, stage: str, done: int, total: int | None) -> None:
        """Etapas del video: preparar → codificar (FFmpeg) → validar."""
        if not self._busy:
            return
        if stage == STAGE_ENCODING and "encoding" not in self._logged_stages:
            self._logged_stages.add("encoding")
            self._log("Codificación H.264 + AAC iniciada")
        if stage == STAGE_VALIDATING and "validating" not in self._logged_stages:
            self._logged_stages.add("validating")
            self._log("Validando archivo final")
        if stage == STAGE_ENCODING and total:
            if self._indeterminate:
                self._stop_indeterminate()
            fraction = min(done / total, 1.0)
            percent = int(fraction * 100)
            self._progress.set(fraction)
            self._percent_label.configure(text=f"{percent}%")
            self._set_status("Codificando H.264 y audio...")
            for milestone in PROGRESS_MILESTONES:
                if percent >= milestone and milestone not in self._logged_milestones:
                    self._logged_milestones.add(milestone)
                    self._log(f"Progreso: {milestone}%")
            return
        messages = {
            STAGE_PREPARING: "Preparando video...",
            STAGE_ENCODING: f"Codificando H.264 y audio... {done} frames",  # total desconocido
            STAGE_VALIDATING: "Validando archivo final...",
        }
        if not self._indeterminate:
            self._start_indeterminate()
        self._percent_label.configure(text="")
        self._set_status(messages.get(stage, "Procesando video..."))

    def _handle_batch_progress(self, done: int, total: int, current_name: str | None) -> None:
        if not self._busy or total < 2 or current_name is None:
            return
        fraction = done / total
        self._progress.set(fraction)
        self._percent_label.configure(text=f"{int(fraction * 100)}%")
        self._set_status(f"Procesando {done + 1} / {total} — {current_name}")

    def _handle_batch_done(self, batch: BatchResult) -> None:
        if not batch.used_batch_folder:  # una sola imagen: mismo comportamiento de siempre
            if batch.succeeded:
                self._handle_done(batch.succeeded[0])
            else:
                self._handle_error(batch.failed[0][1])
            return

        self._finish_progress(1.0 if not batch.cancelled else batch.processed / batch.total)
        ok, failed = len(batch.succeeded), len(batch.failed)
        self._failed_files, self._last_batch = batch.failed, batch
        stats = self._result_panel.show_batch(batch)
        self._show_result()

        for _, reason in batch.failed:
            logger.info("Imagen fallida: %s", reason)
        if batch.cancelled:
            self._set_status(f"Lote cancelado: {ok} de {batch.total} imágenes convertidas.", theme.WARN)
            self._log(f"Lote cancelado: {ok} convertidas, {failed} fallidas", "warn")
        elif ok == 0:
            self._set_status("El lote falló: ninguna imagen se pudo convertir.", theme.ERROR)
            self._log("Ninguna imagen se pudo convertir", "error")
        else:
            self._set_status(f"Lote completo: {ok} correctas, {failed} fallidas.",
                             theme.WARN if failed else theme.ACCENT)
            self._log(f"Lote completo: {ok} correctas, {failed} fallidas", "warn" if failed else "info")
        for path, reason in batch.failed:
            self._log(f"{path.name}: {reason}", "error")
        notes = Counter(warning for item in batch.succeeded for warning in item.warnings)
        for text, count in notes.items():
            self._log(f"{text} ({count} imágenes)", "warn")
        if ok:
            self._log(f"Ahorro: {format_bytes(stats.saved)} ({format_reduction(stats)})"
                      if not stats.is_larger else (larger_warning(stats) or ""), "warn" if stats.is_larger else "info")
            self._history.add(HistoryEntry(
                f"{ok} imágenes", IMAGE_KIND, stats.before, stats.after, batch.output_dir,
                ok=not stats.is_larger, finished_at=None))
            self._history_panel.set_entries(self._history.entries)
            self._request_compare(batch.succeeded[0])

    def _handle_done(self, result: ConversionResult) -> None:
        self._finish_progress(1.0)
        is_video = result.kind == VIDEO_KIND
        stats = self._result_panel.show_video(result) if is_video else self._result_panel.show_image(result)
        self._show_result()
        if is_video:
            self._log("Archivo de salida validado")
        warning = larger_warning(stats)
        for text in result.warnings:
            self._log(text, "warn")
        if warning:
            self._set_status("Conversión completa, pero el archivo resultante es más grande.", theme.WARN)
            self._log(warning, "warn")
        else:
            self._set_status("Conversión completa.", theme.ACCENT)
        self._log(f"Conversión completa en {format_elapsed(result.elapsed_seconds)} → {display_path(result.output_path)}")
        self._history.add(HistoryEntry(
            result.output_path.name, result.kind or "", stats.before, stats.after, result.output_path,
            ok=not stats.is_larger))
        self._history_panel.set_entries(self._history.entries)
        if not is_video:
            self._request_compare(result)

    def _handle_error(self, message: str) -> None:
        self._finish_progress(0.0)
        self._show_error(message, "ERROR DE CONVERSIÓN")
        self._set_status("La conversión falló.", theme.ERROR)

    def _handle_cancelled(self) -> None:
        self._finish_progress(0.0)
        self._set_status("Conversión cancelada. No se guardó ningún archivo.", theme.WARN)
        self._log("Conversión cancelada; archivos temporales eliminados y original intacto", "warn")

    def _finish_progress(self, fraction: float) -> None:
        self._stop_indeterminate()
        self._progress.set(fraction)
        self._percent_label.configure(text=f"{int(fraction * 100)}%" if fraction else "")
        self._set_busy(False)
        if self._started_at:
            self._elapsed_label.configure(text=f"Tiempo: {format_clock(time.monotonic() - self._started_at)}")

    def _on_cancel(self) -> None:
        if not self._busy:
            return
        self._cancel_event.set()
        self._cancel_button.configure(state="disabled")
        self._set_status("Cancelando...", theme.WARN)
        self._log("Cancelando conversión...", "warn")

    # ------------------------------------------------------------ image compare --
    def _select_compare(self, index: int) -> None:
        if self._last_batch is not None and 0 <= index < len(self._last_batch.succeeded):
            self._request_compare(self._last_batch.succeeded[index])

    def _request_compare(self, result: ConversionResult) -> None:
        """Genera miniaturas en un hilo (la imagen completa nunca se carga en la GUI)."""
        if result.source_path is None:
            return
        self._compare_token += 1
        token, self._compare_pending = self._compare_token, True
        self._result_panel.set_compare_message("Generando vista previa...")
        threading.Thread(target=self._compare_worker, args=(token, result), daemon=True).start()
        self._ensure_polling()

    def _compare_worker(self, token: int, result: ConversionResult) -> None:
        try:
            original = load_thumbnail(result.source_path, COMPARE_BOX)
            optimized = load_thumbnail(result.output_path, COMPARE_BOX)
            src_w, src_h = result.source_size or (0, 0)
            out_w, out_h = result.output_size
            self._events.put((
                "compare", token, original, optimized,
                f"{src_w}×{src_h} · {format_bytes(result.source_bytes or 0)}",
                f"{out_w}×{out_h} · {format_bytes(result.output_bytes or 0)}",
            ))
        except Exception:
            logger.exception("No se pudo generar la vista previa de %s", result.output_path)
            self._events.put(("compare", token, None, None, "", ""))

    def _handle_compare(self, token: int, original, optimized, original_caption: str, optimized_caption: str) -> None:
        if token != self._compare_token:
            return
        self._compare_pending = False
        if original is None or optimized is None:
            self._result_panel.set_compare_message("Vista previa no disponible")
        else:
            self._result_panel.set_compare_images(original, optimized, original_caption, optimized_caption)

    # ------------------------------------------------------------ result actions --
    def _show_result(self) -> None:
        self._result_panel.grid()
        self._scroll_to(self._result_panel)

    def _open_path(self, path: Path) -> None:
        target = path if path.exists() else (path.parent if path.parent.exists() else ensure_output_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            logger.exception("No se pudo abrir %s", target)
            self._show_error(f"No se pudo abrir automáticamente. Búscalo en: {target}")

    def _copy_path(self, path: Path) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(path))
        self.update_idletasks()
        self._log(f"Ruta copiada: {path}")

    def _discard_result(self, path: Path) -> None:
        """Borra el archivo de salida (nunca el original) tras confirmación explícita."""
        if self._busy or not path.is_file() or self._result_panel.last_output != path:
            return
        if not messagebox.askyesno(
            "Descartar resultado",
            f"Se eliminará el archivo generado:\n{path}\n\nEl archivo original no se toca. ¿Continuar?",
            parent=self,
        ):
            return
        try:
            path.unlink()
        except OSError:
            logger.exception("No se pudo descartar %s", path)
            self._show_error("No se pudo eliminar el archivo. Quizá está abierto en otro programa.")
            return
        self._history.remove_location(path)
        self._history_panel.set_entries(self._history.entries)
        self._result_panel.hide()
        self._set_status("Resultado descartado. El original sigue intacto.", theme.WARN)
        self._log(f"Resultado descartado: {path.name}", "warn")

    def _show_failed_files(self) -> None:
        """Ventana con la lista completa de archivos que no se pudieron convertir y su motivo."""
        if not self._failed_files:
            return
        dialog = ctk.CTkToplevel(self, fg_color=theme.BG)
        dialog.title("Archivos fallidos")
        dialog.geometry("700x380")
        dialog.transient(self)
        box = ctk.CTkTextbox(dialog, wrap="word", fg_color=theme.CARD_ALT, text_color=theme.TEXT,
                             font=theme.mono_font(12), border_width=1, border_color=theme.BORDER)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        for path, reason in self._failed_files:
            box.insert("end", f"{path}\n    {reason}\n\n")
        box.configure(state="disabled")
        dialog.after(150, dialog.focus)

    # ------------------------------------------------------------- state helpers --
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._cancel_button.configure(state="normal")
            self._cancel_button.grid()
        else:
            self._cancel_button.grid_remove()
        self._update_controls()

    def _update_controls(self) -> None:
        working = self._busy or self._inspecting
        self._dropzone.set_enabled(not working)
        self._config_panel.set_enabled(not working)
        settings, _ = self._config_panel.evaluate()
        ready = settings is not None and bool(self._source_paths) and not working
        set_primary_enabled(self._convert_button, ready)

    def _set_status(self, text: str, color: str = theme.TEXT) -> None:
        self._status_label.configure(text=text, text_color=color)

    def _start_indeterminate(self) -> None:
        if not self._indeterminate:
            self._indeterminate = True
            self._progress.configure(mode="indeterminate")
            self._progress.start()

    def _stop_indeterminate(self) -> None:
        if self._indeterminate:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._indeterminate = False

    def _scroll_to(self, widget) -> None:
        """Desplaza el cuerpo para que ``widget`` quede a la vista (mejor esfuerzo)."""
        def scroll() -> None:
            try:
                self.update_idletasks()
                canvas = self._body._parent_canvas
                bbox = canvas.bbox("all")
                total = (bbox[3] - bbox[1]) if bbox else 0
                if total > canvas.winfo_height() > 0:
                    canvas.yview_moveto(max(0.0, (widget.winfo_y() - 8) / total))
            except Exception:
                logger.debug("No se pudo desplazar la vista", exc_info=True)
        self._jobs.append(self.after(60, scroll))
        del self._jobs[:-20]  # solo interesan los recientes

    def _log(self, message: str, level: str = "info") -> None:
        self._log_view.write(message, level)
        if level == "error":
            logger.error("UI: %s", message)

    def _show_error(self, message: str, title: str = "ERROR AL CARGAR ARCHIVO") -> None:
        """Error visible en la tarjeta de resultados y en el registro (sin ventanas emergentes ni trazas)."""
        self._result_panel.show_error(message, title=title)
        self._show_result()
        self._log(message, "error")

    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askyesno(
                "Media Compressor", "Hay una conversión en curso. ¿Cancelarla y salir?", parent=self
            ):
                return
            self._cancel_event.set()
            if self._worker is not None:
                self._worker.join(timeout=10)
        self._drop.uninstall()
        for job in [*self._jobs, self._poll_job]:
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self.destroy()
