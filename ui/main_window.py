"""Ventana principal (CustomTkinter). Orquesta los servicios sin contener lógica de OpenCV."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from services.common import (
    OUTPUT_DIR,
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    calculate_output_size,
    display_path,
    ensure_output_dir,
    format_duration,
    parse_percentage,
)
from services.image_processor import (
    IMAGE_INPUT_EXTENSIONS,
    available_image_formats,
    convert_image,
    image_size,
    load_image,
    to_rgb_preview,
)
from services.video_processor import (
    VIDEO_INPUT_EXTENSIONS,
    VideoInfo,
    available_video_formats,
    convert_video,
    probe_video,
    video_audio_status,
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


class MainWindow(ctk.CTk):
    """Ventana de la aplicación: carga archivos, pide parámetros y lanza la conversión."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title("Conversor Multimedia")
        height = min(780, self.winfo_screenheight() - 90)  # cabe también en pantallas de 768 px
        self.geometry(f"1000x{height}")
        self.minsize(900, min(660, height))

        self._source_path: Path | None = None
        self._kind: str | None = None
        self._media_size: tuple[int, int] | None = None
        self._busy = False
        self._last_output: Path | None = None
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._events: queue.Queue[tuple] = queue.Queue()
        self._preview_image: ctk.CTkImage | None = None
        self._indeterminate = False

        self._percent_var = ctk.StringVar(value="50")
        self._percent_var.trace_add("write", lambda *_: self._refresh_summary())

        self._build_widgets()
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            ensure_output_dir()
        except ConversionError as exc:
            self._log(str(exc), ERROR_COLOR)

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
        self._open_button = ctk.CTkButton(
            results, text="ABRIR CARPETA DE SALIDA", height=36, command=self._open_output_folder
        )
        self._open_button.grid(row=2, column=0, pady=(8, 0), sticky="e")
        self._open_button.grid_remove()

    def _build_panel(self) -> None:
        self._preview_label = ctk.CTkLabel(
            self._panel, text="", width=PREVIEW_MAX_SIZE[0], height=PREVIEW_MAX_SIZE[1],
            fg_color=("gray85", "gray20"), corner_radius=8,
        )
        self._preview_label.grid(row=0, column=0, rowspan=2, padx=16, pady=16, sticky="n")

        info = ctk.CTkFrame(self._panel, fg_color="transparent")
        info.grid(row=0, column=1, padx=(0, 16), pady=(16, 0), sticky="nsew")
        info.grid_columnconfigure(0, weight=1)
        self._file_label = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480,
                                        font=ctk.CTkFont(weight="bold"))
        self._file_label.grid(row=0, column=0, sticky="ew")
        self._metadata_label = ctk.CTkLabel(info, text="", anchor="w", justify="left")
        self._metadata_label.grid(row=1, column=0, pady=(6, 0), sticky="ew")

        ctk.CTkLabel(info, text="¿Qué porcentaje deseas reducir?", anchor="w").grid(
            row=2, column=0, pady=(8, 2), sticky="ew"
        )
        percent_row = ctk.CTkFrame(info, fg_color="transparent")
        percent_row.grid(row=3, column=0, sticky="w")
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
        self._summary_label.grid(row=4, column=0, pady=(6, 0), sticky="ew")

        ctk.CTkLabel(info, text="Formato de salida", anchor="w").grid(row=5, column=0, pady=(8, 2), sticky="ew")
        self._format_selector = ctk.CTkSegmentedButton(info, values=["-"])
        self._format_selector.grid(row=6, column=0, sticky="w")

        self._notice_label = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=480,
                                          text_color=WARN_COLOR)
        self._notice_label.grid(row=7, column=0, pady=(6, 0), sticky="ew")

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
        self._choose_file(
            IMAGE_KIND,
            "Seleccionar imagen",
            [("Imágenes", _pattern(IMAGE_INPUT_EXTENSIONS)), ("Todos los archivos", "*.*")],
        )

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
                formats = list(available_video_formats())
                if not formats:
                    raise ConversionError(
                        "Esta instalación de OpenCV no puede codificar video en ningún formato compatible."
                    )
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
        self._file_label.configure(text=f"Archivo seleccionado: {path.name}")
        self._set_preview(preview)
        extension = path.suffix.lstrip(".").upper()
        self._configure_formats(formats, {"JPEG": "JPG", "TIF": "TIFF"}.get(extension, extension))
        self._notice_label.configure(text="")
        if kind == VIDEO_KIND:
            _, warning = video_audio_status()
            if warning:
                self._notice_label.configure(text=warning)
        self._metadata_label.configure(text=metadata)
        self._empty_label.grid_remove()
        self._panel.grid()
        self._open_button.grid_remove()
        self._reset_progress()
        self._refresh_summary()
        self._log(f"Archivo cargado ({kind}): {path.name} ({size[0]} × {size[1]} px)")
        return True

    @staticmethod
    def _video_metadata(info: VideoInfo) -> str:
        frames = str(info.frame_count) if info.frame_count else "desconocido"
        return f"FPS: {info.fps:g}     Frames: {frames}     Duración: {format_duration(info.duration_seconds)}"

    def _configure_formats(self, formats: list[str], preferred: str) -> None:
        self._format_selector.configure(values=formats)
        self._format_selector.set(preferred if preferred in formats else formats[0])

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

    def _refresh_summary(self) -> None:
        if self._media_size is None:
            return
        width, height = self._media_size
        try:
            percent = parse_percentage(self._percent_var.get())
        except ConversionError as exc:
            self._summary_label.configure(text=str(exc), text_color=ERROR_COLOR)
            self._update_controls()
            return

        is_video = self._kind == VIDEO_KIND
        out_width, out_height = calculate_output_size(width, height, percent, even=is_video)
        raw_width, raw_height = calculate_output_size(width, height, percent)
        text = (
            f"Resolución original: {width} × {height} px\n"
            f"Reducción: {percent:g} %  (cada lado queda en {100 - percent:g} % de su tamaño original)\n"
            f"Resolución de salida: {out_width} × {out_height} px"
        )
        if is_video and (out_width, out_height) != (raw_width, raw_height):
            text += "\n(Ajustada a valores pares, requeridos por los códecs de video.)"
        self._summary_label.configure(text=text, text_color=("gray10", "gray90"))
        self._update_controls()

    # --------------------------------------------------------- conversion --
    def _on_convert(self) -> None:
        if self._busy or self._source_path is None or self._kind is None:
            return
        percent = self._current_percentage()
        if percent is None:
            self._show_error("Ingresa un porcentaje válido entre 1 y 99 antes de convertir.")
            return
        format_label = self._format_selector.get()
        source, kind = self._source_path, self._kind

        self._cancel_event = threading.Event()
        self._set_busy(True)
        self._open_button.grid_remove()
        self._reset_progress()
        if kind == VIDEO_KIND:
            self._status_label.configure(text="Procesando video... 0%", text_color=("gray10", "gray90"))
        else:
            self._status_label.configure(text="Procesando imagen...", text_color=("gray10", "gray90"))
            self._start_indeterminate()
        self._log(f"Iniciando conversión de {source.name} → {format_label}, reducción {percent:g} %")

        self._worker = threading.Thread(
            target=self._run_worker, args=(source, kind, percent, format_label, self._cancel_event), daemon=True
        )
        self._worker.start()

    def _run_worker(self, source: Path, kind: str, percent: float, format_label: str,
                    cancel_event: threading.Event) -> None:
        """Se ejecuta en un hilo: solo comunica con la GUI mediante la cola de eventos."""
        try:
            if kind == IMAGE_KIND:
                result = convert_image(source, percent, format_label)
            else:
                result = convert_video(
                    source, percent, format_label,
                    progress_callback=lambda done, total: self._events.put(("progress", done, total)),
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
                    "done": self._handle_done,
                    "error": self._handle_error,
                    "cancelled": self._handle_cancelled,
                }[event[0]]
                handler(*event[1:])
        except queue.Empty:
            pass
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll_events)

    def _handle_progress(self, done: int, total: int | None) -> None:
        if not self._busy:
            return
        if total:
            if self._indeterminate:
                self._stop_indeterminate()
            fraction = min(done / total, 1.0)
            self._progress.set(fraction)
            self._status_label.configure(text=f"Procesando video... {int(fraction * 100)}%")
        else:
            if not self._indeterminate:
                self._start_indeterminate()
            self._status_label.configure(text=f"Procesando video... {done} frames")

    def _handle_done(self, result: ConversionResult) -> None:
        self._stop_indeterminate()
        self._progress.set(1)
        self._set_busy(False)
        self._last_output = result.output_path
        self._status_label.configure(text="Conversión completada correctamente.", text_color=OK_COLOR)
        lines = [
            "Conversión completada correctamente.",
            f"Archivo de salida: {display_path(result.output_path)}",
            f"Ruta completa: {result.output_path}",
            f"Resolución de salida: {result.output_size[0]} × {result.output_size[1]} px",
        ]
        if result.audio_preserved is True:
            lines.append("Audio original conservado.")
        lines += [f"Aviso: {warning}" for warning in result.warnings]
        self._log("\n".join(lines), OK_COLOR)
        self._open_button.grid()

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
        if busy and self._kind == VIDEO_KIND:
            self._cancel_button.configure(state="normal")
            self._cancel_button.grid()
        else:
            self._cancel_button.grid_remove()
        self._update_controls()

    def _update_controls(self) -> None:
        idle = "disabled" if self._busy else "normal"
        for widget in (self._video_button, self._image_button, self._percent_entry,
                       self._format_selector, *self._preset_buttons):
            widget.configure(state=idle)
        valid = self._current_percentage() is not None and self._source_path is not None
        self._convert_button.configure(state="normal" if valid and not self._busy else "disabled")

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
        if color is None:
            self._results_box.insert("end", message + "\n\n")
        else:
            tag = f"color_{color[0]}"
            light, dark = color  # tag_config no resuelve pares claro/oscuro por sí solo
            self._results_box.tag_config(tag, foreground=light if ctk.get_appearance_mode() == "Light" else dark)
            self._results_box.insert("end", message + "\n\n", tag)
        self._results_box.see("end")
        self._results_box.configure(state="disabled")

    def _show_error(self, message: str) -> None:
        self._log(f"Error: {message}", ERROR_COLOR)
        messagebox.showerror("Conversor Multimedia", message, parent=self)

    def _open_output_folder(self) -> None:
        folder = ensure_output_dir()
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
