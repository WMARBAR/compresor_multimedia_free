"""Pruebas de la interfaz (necesitan pantalla; se omiten si Tk no puede abrir una ventana).

Ejecutar:  python -m unittest discover -s tests -v
Todas las conversiones se escriben en carpetas temporales: nunca se toca ``outs/`` del proyecto.
"""

from __future__ import annotations

import functools
import hashlib
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np

from test_services import make_av_video, make_test_image

try:
    import customtkinter as ctk

    from ui import main_window
    from ui.config_panel import ConfigPanel
    from ui.main_window import MainWindow
    from ui.widgets import shorten_middle

    _probe = ctk.CTk()
    _probe.destroy()
    HAS_DISPLAY = True
except Exception:  # sin Tk o sin pantalla
    HAS_DISPLAY = False


def pump(app, seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def wait_idle(app, timeout: float = 120.0) -> None:
    end = time.time() + timeout
    while (app._busy or app._inspecting or app._compare_pending) and time.time() < end:
        pump(app, 0.05)
    pump(app, 0.4)


@unittest.skipUnless(HAS_DISPLAY, "no hay pantalla disponible para Tk")
class ShortenTests(unittest.TestCase):
    def test_long_names_keep_extension_and_limit(self):
        name = "una_fotografia_con_un_nombre_extremadamente_largo_2026_final_v3.png"
        shortened = shorten_middle(name, 30)
        self.assertLessEqual(len(shortened), 30)
        self.assertTrue(shortened.endswith(".png"))
        self.assertIn("…", shortened)
        self.assertEqual(shorten_middle("corto.png", 30), "corto.png")


@unittest.skipUnless(HAS_DISPLAY, "no hay pantalla disponible para Tk")
class ConfigPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.changes = 0
        self.panel = ConfigPanel(self.root, self._changed)
        self.panel.pack()
        self.addCleanup(self.root.destroy)

    def _changed(self):
        self.changes += 1

    def video(self, size=(1920, 1080), audio=True):
        self.panel.set_media("video", size, ["MP4", "MOV"], "MP4", has_audio=audio)

    def warnings(self) -> str:
        return self.panel._warnings.cget("text")

    def test_default_video_profile_is_high_quality_and_keeps_resolution(self):
        self.video()
        settings, error = self.panel.evaluate()
        self.assertIsNone(error)
        self.assertEqual((settings.quality.key, settings.quality.crf), ("high", 20))
        self.assertEqual(self.panel._resolution_label.cget("text"), "1920 × 1080 → 1920 × 1080")
        self.assertIn("100%", self.panel._scale_label.cget("text"))
        self.assertEqual(self.warnings(), "")

    def test_web_profile_shows_downscale_and_never_upscales(self):
        self.video()
        self.panel._profile.set("Web optimizado")
        self.panel._refresh()
        self.assertEqual(self.panel._resolution_label.cget("text"), "1920 × 1080 → 1280 × 720")
        self.assertIn("66.7% resolución lineal", self.panel._scale_label.cget("text"))
        self.video((640, 360))
        self.panel._profile.set("Web optimizado")
        self.panel._refresh()
        self.assertEqual(self.panel._resolution_label.cget("text"), "640 × 360 → 640 × 360")

    def test_high_compression_warns_about_quality_and_resolution(self):
        self.video()
        self.panel._profile.set("Compresión alta")
        self.panel._refresh()
        self.assertIn("Puede reducir notablemente la nitidez del video.", self.warnings())
        self.assertIn("La resolución se reducirá significativamente.", self.warnings())
        self.assertEqual(self.panel._resolution_label.cget("text"), "1920 × 1080 → 960 × 540")

    def test_custom_crf_warnings(self):
        self.video()
        self.panel._profile.set("Personalizado")
        for crf, expected in (("21", None), ("25", "artefactos"), ("28", "pérdida considerable")):
            self.panel._crf.set(crf)
            if expected is None:
                self.assertNotIn("artefactos", self.warnings())
                self.assertNotIn("pérdida considerable", self.warnings())
            else:
                self.assertIn(expected, self.warnings())

    def test_custom_validation_blocks_conversion(self):
        self.video()
        self.panel._profile.set("Personalizado")
        for bad in ("abc", "-5", "100", ""):
            self.panel._video_reduction.set(bad)
            settings, error = self.panel.evaluate()
            self.assertIsNone(settings, bad)
            self.assertTrue(error)
            self.assertIn("✗", self.warnings())
        self.panel._video_reduction.set("30")
        self.assertIsNotNone(self.panel.evaluate()[0])

    def test_advanced_values_only_apply_when_opened(self):
        self.video()
        self.panel._profile.set("Personalizado")
        self.panel._max_width.set("640")
        self.panel._preset.set("slow")
        settings, _ = self.panel.evaluate()
        self.assertEqual((settings.quality.preset, settings.quality.max_width), ("medium", None))
        self.panel._toggle_advanced()
        settings, _ = self.panel.evaluate()
        self.assertEqual((settings.quality.preset, settings.quality.max_width), ("slow", 640))
        self.assertEqual(settings.quality.target_size(1920, 1080), (640, 360))

    def test_ffmpeg_missing_blocks_video_but_not_images(self):
        self.panel.set_ffmpeg_error("FFmpeg no está disponible.")
        self.video()
        settings, error = self.panel.evaluate()
        self.assertIsNone(settings)
        self.assertIn("FFmpeg", error)
        self.panel.set_ffmpeg_error(None)
        self.panel.set_media("imagen", (800, 600), ["JPG", "PNG"], "PNG")
        self.assertIsNotNone(self.panel.evaluate()[0])

    def test_image_settings_and_warnings(self):
        self.panel.set_media("imagen", (1600, 1200), ["JPG", "PNG", "WEBP"], "PNG", has_alpha=True)
        settings, _ = self.panel.evaluate()
        self.assertEqual((settings.percent, settings.format_label, settings.image_quality), (50.0, "PNG", None))
        self.assertEqual(self.panel._resolution_label.cget("text"), "1600 × 1200 → 800 × 600")
        self.assertIn("La resolución se reducirá significativamente.", self.warnings())
        self.panel._format_selector.set("JPG")
        self.panel._refresh()
        self.assertIn("transparencia", self.warnings())
        self.panel._image_percent.set("20")
        self.assertNotIn("significativamente", self.warnings())
        self.panel._toggle_advanced()
        self.panel._image_quality.set("40")
        self.assertEqual(self.panel.evaluate()[0].image_quality, 40)
        self.panel._image_quality.set("0")
        self.assertIsNone(self.panel.evaluate()[0])
        self.panel._image_percent.set("100")
        self.assertIsNone(self.panel.evaluate()[0])

    def test_only_supported_formats_are_offered(self):
        self.panel.set_media("imagen", (100, 100), ["JPG", "PNG"], "PNG")
        self.assertEqual(self.panel._format_selector.cget("values"), ["JPG", "PNG"])
        self.assertEqual(self.panel.format_label, "PNG")


@unittest.skipUnless(HAS_DISPLAY, "no hay pantalla disponible para Tk")
class MainWindowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.addCleanup(self._tmp.cleanup)
        patches = [
            mock.patch.object(main_window, "convert_video", functools.partial(main_window.convert_video, output_dir=self.out)),
            mock.patch.object(main_window, "convert_images", functools.partial(main_window.convert_images, output_dir=self.out)),
            mock.patch.object(main_window.messagebox, "askyesno", return_value=True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.app = MainWindow()
        self.app.geometry("1280x800")
        self.addCleanup(self._close)
        pump(self.app)

    def _close(self):
        try:
            self.app._on_close()
        except Exception:
            pass

    def log(self) -> str:
        return "\n".join(self.app._log_view.lines())

    def load(self, *paths):
        self.assertTrue(self.app._load_paths(list(paths)))
        wait_idle(self.app)

    def test_startup_state(self):
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertIn("FFMPEG ONLINE", self.app._ffmpeg_pill.cget("text"))
        self.assertIn("FFmpeg", self.log())
        self.assertIsNone(self.app._poll_job, "no debe haber sondeo en reposo")
        self.assertFalse(self.app._result_panel.winfo_ismapped())

    def test_ffmpeg_offline_blocks_video_only(self):
        message = "FFmpeg no está disponible. La conversión de video requiere FFmpeg."
        self.app._on_close()
        with mock.patch.object(main_window, "ffmpeg_status", return_value=(None, message)):
            self.app = MainWindow()
        pump(self.app)
        self.assertIn("OFFLINE", self.app._ffmpeg_pill.cget("text"))
        self.assertIn(message, self.log())
        video = self.tmp / "v.mp4"
        make_av_video(video, 320, 240, 1, 30, True)
        self.load(video)
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertIn("FFmpeg", self.app._config_panel._warnings.cget("text"))
        image = self.tmp / "i.png"
        make_test_image(image, 200, 100)
        self.load(image)
        self.assertEqual(self.app._convert_button.cget("state"), "normal")

    def test_single_image_flow_with_result_and_history(self):
        image = self.tmp / "foto.png"
        make_test_image(image, 1600, 900)
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        self.load(image)
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        self.assertIn(("Resolución", "1600 × 900 px"), self.app._meta.rows())
        self.app._on_convert()
        wait_idle(self.app)
        output = self.out / "foto_convertido.png"
        self.assertTrue(output.exists())
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), digest, "el original no debe cambiar")
        self.assertIn("CONVERSIÓN COMPLETA", self.app._result_panel.title_label.cget("text"))
        self.assertEqual(len(self.app._history), 1)
        self.assertIsNotNone(self.app._result_panel._compare._original, "debe haber miniaturas de comparación")
        self.assertIn("Conversión completa", self.log())
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        self.assertIsNone(self.app._poll_job, "el sondeo se detiene al terminar")

    def test_batch_with_corrupt_image_continues(self):
        good = []
        for number in range(3):
            path = self.tmp / f"i{number}.png"
            make_test_image(path, 400 + number * 50, 300)
            good.append(path)
        bad = self.tmp / "rota.jpg"
        bad.write_bytes(b"corrupta")
        self.load(*good, bad)
        self.assertEqual(len(self.app._source_paths), 4)
        self.app._on_convert()
        wait_idle(self.app)
        batch = self.app._last_batch
        self.assertEqual((len(batch.succeeded), len(batch.failed)), (3, 1))
        self.assertIn("LOTE COMPLETO CON ERRORES", self.app._result_panel.title_label.cget("text"))
        self.assertEqual(len(self.app._history), 1)
        self.assertTrue(batch.output_dir.is_dir())

    def test_discard_result_removes_only_the_output(self):
        image = self.tmp / "foto.png"
        make_test_image(image, 400, 300)
        self.load(image)
        self.app._on_convert()
        wait_idle(self.app)
        output = self.app._result_panel.last_output
        self.assertTrue(output.exists())
        self.app._discard_result(output)
        self.assertFalse(output.exists())
        self.assertTrue(image.exists())
        self.assertEqual(len(self.app._history), 0)

    def test_larger_result_is_flagged_and_kept(self):
        small = self.tmp / "ruido.jpg"
        noise = (np.random.default_rng(3).random((200, 300, 3)) * 255).astype(np.uint8)
        cv2.imwrite(str(small), noise, [cv2.IMWRITE_JPEG_QUALITY, 30])
        self.load(small)
        self.app._config_panel._format_selector.set("PNG")
        self.app._config_panel._image_percent.set("1")
        pump(self.app)
        self.app._on_convert()
        wait_idle(self.app)
        self.assertTrue((self.out / "ruido_convertido.png").exists(), "no se borra automáticamente")
        self.assertIn("MÁS GRANDE", self.app._result_panel.title_label.cget("text"))
        self.assertIn("más grande que el original", self.log())
        self.assertFalse(self.app._history.entries[0].ok)

    def test_invalid_inputs_show_error_card_without_modal(self):
        broken = self.tmp / "roto.mp4"
        broken.write_bytes(b"no soy un video")
        with mock.patch.object(main_window.messagebox, "showerror") as showerror:
            self.assertTrue(self.app._load_paths([broken]))
            wait_idle(self.app)
            self.assertFalse(self.app._load_paths([self.tmp / "notas.txt"]))
        showerror.assert_not_called()
        self.assertIn("ERROR", self.app._result_panel.title_label.cget("text"))
        self.assertNotIn("Traceback", self.log())
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")

    def test_video_with_and_without_audio_and_result_card(self):
        with_audio, silent = self.tmp / "a.mp4", self.tmp / "s.mp4"
        make_av_video(with_audio, 1280, 720, 2, 30, True)
        make_av_video(silent, 1280, 720, 2, 30, False)
        digest = hashlib.sha256(with_audio.read_bytes()).hexdigest()
        self.load(with_audio)
        self.assertIn(("Audio", "AAC"), self.app._meta.rows())
        self.app._config_panel._profile.set("Web optimizado")
        self.app._config_panel._refresh()
        self.app._on_convert()
        wait_idle(self.app)
        self.assertTrue((self.out / "a_convertido.mp4").exists())
        self.assertIn("CONVERSIÓN COMPLETA", self.app._result_panel.title_label.cget("text"))
        self.assertIn("Archivo de salida validado", self.log())
        self.assertEqual(hashlib.sha256(with_audio.read_bytes()).hexdigest(), digest)
        self.load(silent)
        self.assertIn(("Audio", "NINGUNO"), self.app._meta.rows())
        self.app._on_convert()
        wait_idle(self.app)
        self.assertTrue((self.out / "s_convertido.mp4").exists())
        self.assertEqual(len(self.app._history), 2)

    def test_cancel_restores_ui_and_cleans_up(self):
        long_video = self.tmp / "largo.mp4"
        make_av_video(long_video, 1280, 720, 20, 30, True)
        self.load(long_video)
        self.app._config_panel._profile.set("Máxima calidad")
        self.app._config_panel._refresh()
        self.app._on_convert()
        pump(self.app, 1.5)
        self.assertTrue(self.app._busy)
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.app._on_cancel()
        wait_idle(self.app, 60)
        self.assertFalse(self.app._busy)
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        self.assertEqual(list(self.out.glob("largo*")) if self.out.exists() else [], [])
        self.assertIn("cancelada", self.log().lower())
        self.assertTrue(long_video.exists())

    @unittest.skipUnless(sys.platform.startswith("win"), "arrastrar y soltar nativo solo en Windows")
    def test_drop_files_message_loads_media(self):
        import ctypes
        from ctypes import wintypes

        first, second = self.tmp / "a.png", self.tmp / "b.png"
        make_test_image(first, 300, 200)
        make_test_image(second, 400, 300)
        self.assertTrue(self.app._drop_active or self.app._drop.install(), "el destino de archivos debe instalarse")
        kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = ctypes.c_void_p
        kernel.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        names = "\0".join(str(path) for path in (first, second)) + "\0\0"
        blob = struct.pack("<IiiII", 20, 0, 0, 0, 1) + names.encode("utf-16-le")  # DROPFILES + rutas anchas
        handle = kernel.GlobalAlloc(0x42, len(blob))
        pointer = kernel.GlobalLock(handle)
        ctypes.memmove(pointer, blob, len(blob))
        kernel.GlobalUnlock(handle)
        user.PostMessageW(self.app._drop._installed[0], 0x0233, handle, 0)  # WM_DROPFILES
        self.app.after(2000, self.app.quit)
        self.app.mainloop()  # la entrega cruza hilos y necesita el bucle principal
        wait_idle(self.app)
        self.assertEqual((self.app._kind, len(self.app._source_paths)), ("imagen", 2))

    def test_open_and_copy_actions(self):
        image = self.tmp / "foto.png"
        make_test_image(image, 400, 300)
        self.load(image)
        self.app._on_convert()
        wait_idle(self.app)
        output = self.app._result_panel.last_output
        with mock.patch.object(main_window.os, "startfile", create=True) as startfile:
            self.app._open_path(output)
            self.app._open_path(output.parent)
        self.assertEqual([call.args[0] for call in startfile.call_args_list], [output, output.parent])
        self.app._copy_path(output)
        self.assertEqual(self.app.clipboard_get(), str(output))

    def test_buttons_are_keyboard_accessible(self):
        calls = []
        button = self.app._dropzone.video_button
        button.configure(command=lambda: calls.append("video"))
        canvas = button._canvas
        self.assertEqual(str(canvas.cget("takefocus")), "1")
        canvas.focus_force()
        pump(self.app)
        self.assertIs(self.app.focus_get(), canvas)
        self.assertEqual(button.cget("border_width"), 2, "el foco debe verse")
        canvas.event_generate("<space>")
        pump(self.app)
        self.assertEqual(calls, ["video"])
        self.assertEqual(str(self.app._convert_button._canvas.cget("takefocus")), "0", "deshabilitado: sin foco")

    def test_keyboard_shortcuts_are_bound(self):
        bound = self.app.bind()
        for sequence in ("<Control-Key-o>", "<Control-Key-Return>", "<Key-Escape>"):
            self.assertIn(sequence, bound)


if __name__ == "__main__":
    unittest.main()
