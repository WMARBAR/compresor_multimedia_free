"""Pruebas de los servicios. Ejecutar:  python -m unittest discover -s tests -v"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ffmpeg_tools, video_processor
from services.common import (
    OUTPUT_DIR,
    PROJECT_ROOT,
    ConversionCancelled,
    ConversionError,
    calculate_output_size,
    parse_percentage,
    reserve_output_path,
)
from services.image_processor import available_image_formats, convert_image, load_image


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_test_image(path: Path, width: int = 1920, height: int = 1080, alpha: bool = False) -> None:
    channels = 4 if alpha else 3
    image = np.zeros((height, width, channels), dtype=np.uint8)
    image[:, : width // 2, :3] = (255, 0, 0)
    image[:, width // 2 :, :3] = (0, 255, 0)
    if alpha:
        image[:, :, 3] = 0
        image[: height // 2, :, 3] = 255
    ok, encoded = cv2.imencode(path.suffix, image)
    assert ok
    path.write_bytes(encoded.tobytes())


def make_test_video(path: Path, width: int = 320, height: int = 240, frames: int = 30, fps: float = 15.0) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    assert writer.isOpened()
    for index in range(frames):
        frame = np.full((height, width, 3), (index * 8) % 255, dtype=np.uint8)
        cv2.circle(frame, (20 + index * 5, height // 2), 25, (0, 0, 255), -1)
        writer.write(frame)
    writer.release()


class PercentageTests(unittest.TestCase):
    def test_valid_values(self):
        for text, expected in [("1", 1), ("50", 50), (" 75 % ", 75), ("99", 99), ("12,5", 12.5), ("33.3", 33.3)]:
            self.assertEqual(parse_percentage(text), expected)

    def test_invalid_values(self):
        for text in ["", "  ", "0", "-5", "100", "150", "abc", "nan", "inf", "-inf", "5e1", "1 2", "%", "99.9999", "0.5"]:
            with self.subTest(text=text), self.assertRaises(ConversionError):
                parse_percentage(text)

    def test_output_size(self):
        self.assertEqual(calculate_output_size(1920, 1080, 50), (960, 540))
        self.assertEqual(calculate_output_size(1920, 1080, 25), (1440, 810))
        self.assertEqual(calculate_output_size(1920, 1080, 75), (480, 270))
        self.assertEqual(calculate_output_size(1, 1, 99), (1, 1))

    def test_even_adjustment(self):
        self.assertEqual(calculate_output_size(1921, 1081, 50, even=True), (960, 540))
        width, height = calculate_output_size(7, 5, 90, even=True)
        self.assertTrue(width % 2 == 0 and height % 2 == 0 and width >= 2 and height >= 2)


class OutputNamingTests(unittest.TestCase):
    def test_naming_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outs"  # no existe todavía: debe crearse
            names = [reserve_output_path(Path("C:/x/video_endoscopia.mp4"), ".mp4", out).name for _ in range(4)]
            self.assertEqual(
                names,
                ["video_endoscopia_convertido.mp4", "video_endoscopia_convertido_1.mp4",
                 "video_endoscopia_convertido_2.mp4", "video_endoscopia_convertido_3.mp4"],
            )
            self.assertEqual(reserve_output_path(Path("herida_paciente.png"), "JPG", out).name,
                             "herida_paciente_convertido.jpg")

    def test_output_dir_is_project_root_relative(self):
        self.assertEqual(OUTPUT_DIR, PROJECT_ROOT / "outs")
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parent.parent)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.addCleanup(self._tmp.cleanup)

    def test_all_formats_available(self):
        self.assertEqual(set(available_image_formats()), {"JPG", "PNG", "WEBP", "BMP", "TIFF"})

    def test_multiple_percentages_and_formats(self):
        source = self.tmp / "foto.png"
        make_test_image(source)
        original_hash = sha256(source)
        for percent in (25, 50, 75, 99, 1):
            for label, ext in [("JPG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp"), ("BMP", ".bmp"), ("TIFF", ".tiff")]:
                with self.subTest(percent=percent, fmt=label):
                    result = convert_image(source, percent, label, self.out)
                    expected = calculate_output_size(1920, 1080, percent)
                    decoded = load_image(result.output_path)
                    self.assertEqual((decoded.shape[1], decoded.shape[0]), expected)
                    self.assertEqual(result.output_size, expected)
                    self.assertEqual(result.output_path.suffix, ext)
                    self.assertTrue(result.output_path.name.startswith("foto_convertido"))
        self.assertEqual(sha256(source), original_hash)
        self.assertEqual(calculate_output_size(1920, 1080, 50), (960, 540))

    def test_aspect_ratio_not_cropped(self):
        source = self.tmp / "wide.png"
        make_test_image(source, 1000, 300)
        result = convert_image(source, 50, "PNG", self.out)
        decoded = load_image(result.output_path)
        self.assertEqual(decoded.shape[:2], (150, 500))
        # Ambos lados de la imagen (azul a la izquierda, verde a la derecha) siguen presentes.
        self.assertTrue((decoded[75, 5] == (255, 0, 0)).all())
        self.assertTrue((decoded[75, 495] == (0, 255, 0)).all())

    def test_alpha_to_jpg_uses_white_background(self):
        source = self.tmp / "alpha.png"
        make_test_image(source, 100, 100, alpha=True)
        result = convert_image(source, 50, "JPG", self.out)
        decoded = load_image(result.output_path)
        self.assertEqual(decoded.shape[2], 3)
        self.assertGreater(decoded[45:, :].min(), 240)  # parte transparente -> blanco
        self.assertTrue(result.warnings)

    def test_alpha_preserved_in_png(self):
        source = self.tmp / "alpha.png"
        make_test_image(source, 100, 100, alpha=True)
        decoded = load_image(convert_image(source, 50, "PNG", self.out).output_path)
        self.assertEqual(decoded.shape[2], 4)

    def test_grayscale(self):
        source = self.tmp / "gray.png"
        cv2.imwrite(str(source), np.tile(np.arange(100, dtype=np.uint8), (80, 1)))
        for label in ("JPG", "PNG", "WEBP", "BMP", "TIFF"):
            with self.subTest(fmt=label):
                result = convert_image(source, 50, label, self.out)
                self.assertEqual(load_image(result.output_path).shape[:2], (40, 50))

    def test_sixteen_bit(self):
        source = self.tmp / "deep.png"
        ok, enc = cv2.imencode(".png", (np.random.rand(60, 60, 3) * 65535).astype(np.uint16))
        source.write_bytes(enc.tobytes())
        for label in ("JPG", "PNG", "TIFF"):
            with self.subTest(fmt=label):
                self.assertEqual(load_image(convert_image(source, 50, label, self.out).output_path).shape[:2], (30, 30))

    def test_detects_content_not_extension(self):
        real_png = self.tmp / "real.png"
        make_test_image(real_png, 100, 100)
        disguised = self.tmp / "sin_extension.dat"
        disguised.write_bytes(real_png.read_bytes())
        self.assertEqual(convert_image(disguised, 50, "PNG", self.out).output_size, (50, 50))

    def test_unicode_path(self):
        source = self.tmp / "niño_herida ñandú.png"
        make_test_image(source, 100, 100)
        result = convert_image(source, 50, "PNG", self.out)
        self.assertEqual(result.output_path.name, "niño_herida ñandú_convertido.png")
        self.assertTrue(result.output_path.exists())

    def test_corrupt_and_unsupported_inputs(self):
        garbage = self.tmp / "roto.png"
        garbage.write_bytes(b"esto no es una imagen" * 10)
        empty = self.tmp / "vacio.jpg"
        empty.write_bytes(b"")
        truncated = self.tmp / "truncado.png"
        make_test_image(self.tmp / "ok.png", 200, 200)
        truncated.write_bytes((self.tmp / "ok.png").read_bytes()[:50])
        for path in (garbage, empty, truncated, self.tmp / "no_existe.png"):
            with self.subTest(path=path.name), self.assertRaises(ConversionError):
                convert_image(path, 50, "PNG", self.out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()), "no deben quedar salidas parciales")

    def test_invalid_percentage_and_format(self):
        source = self.tmp / "a.png"
        make_test_image(source, 100, 100)
        for bad in (0, -1, 100, "abc", float("nan")):
            with self.subTest(percent=bad), self.assertRaises(ConversionError):
                convert_image(source, bad, "PNG", self.out)
        with self.assertRaises(ConversionError):
            convert_image(source, 50, "GIF", self.out)

    def test_duplicate_outputs_not_overwritten(self):
        source = self.tmp / "dup.png"
        make_test_image(source, 100, 100)
        paths = [convert_image(source, 50, "PNG", self.out).output_path.name for _ in range(3)]
        self.assertEqual(paths, ["dup_convertido.png", "dup_convertido_1.png", "dup_convertido_2.png"])

    def test_write_failure_cleans_reserved_file(self):
        source = self.tmp / "w.png"
        make_test_image(source, 100, 100)
        with mock.patch.object(Path, "write_bytes", side_effect=PermissionError("denegado")):
            with self.assertRaises(ConversionError):
                convert_image(source, 50, "PNG", self.out)
        self.assertEqual(list(self.out.iterdir()), [])


class VideoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.addCleanup(self._tmp.cleanup)
        self.source = self.tmp / "clip.mp4"
        make_test_video(self.source)

    def test_probe(self):
        info = video_processor.probe_video(self.source)
        self.assertEqual((info.width, info.height), (320, 240))
        self.assertAlmostEqual(info.fps, 15.0, places=1)
        self.assertEqual(info.frame_count, 30)
        self.assertAlmostEqual(info.duration_seconds, 2.0, places=1)

    def test_formats_only_if_encodable(self):
        formats = video_processor.available_video_formats()
        self.assertIn("MP4", formats)
        print("\nFormatos de video disponibles:", formats, "| códecs:", {e: video_processor._working_fourcc(e) for e in formats.values()})

    def test_convert_each_format(self):
        original_hash = sha256(self.source)
        for label, ext in video_processor.available_video_formats().items():
            for percent in (25, 50, 75):
                with self.subTest(fmt=label, percent=percent):
                    reports = []
                    result = video_processor.convert_video(
                        self.source, percent, label, lambda done, total: reports.append((done, total)), None, self.out
                    )
                    expected = calculate_output_size(320, 240, percent, even=True)
                    self.assertEqual(result.output_path.suffix, ext)
                    capture = cv2.VideoCapture(str(result.output_path))
                    self.assertEqual((int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))), expected)
                    self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS), 15.0, places=1)
                    self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 30)
                    capture.release()
                    self.assertEqual(result.frames_processed, 30)
                    self.assertEqual(reports[-1], (30, 30))
        self.assertEqual(sha256(self.source), original_hash)
        leftovers = [p.name for p in self.out.iterdir() if "_convertido" not in p.name]
        self.assertEqual(leftovers, [])

    def test_odd_dimensions_become_even(self):
        odd = self.tmp / "odd.mp4"
        make_test_video(odd, 321, 241, frames=5)
        # mp4v/H.264 no admiten lados impares en todos los casos: el servicio debe ajustarlos.
        result = video_processor.convert_video(odd, 50, "MP4", output_dir=self.out)
        self.assertTrue(result.output_size[0] % 2 == 0 and result.output_size[1] % 2 == 0)

    def test_warns_without_ffmpeg(self):
        with mock.patch.object(video_processor, "find_ffmpeg", return_value=None):
            result = video_processor.convert_video(self.source, 50, "MP4", output_dir=self.out)
        self.assertIn(ffmpeg_tools.FFMPEG_MISSING_WARNING, result.warnings)
        self.assertFalse(result.audio_preserved)
        self.assertEqual(video_processor.video_audio_status()[0], ffmpeg_tools.find_ffmpeg() is not None)

    def test_cancel_cleans_everything(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ConversionCancelled):
            video_processor.convert_video(self.source, 50, "MP4", cancel_event=event, output_dir=self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_cancel_mid_conversion(self):
        event = threading.Event()
        def stop_after_first(done, total):
            event.set()
        with self.assertRaises(ConversionCancelled):
            video_processor.convert_video(self.source, 50, "MP4", stop_after_first, event, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_corrupt_and_invalid_videos(self):
        garbage = self.tmp / "roto.mp4"
        garbage.write_bytes(b"no soy un video" * 100)
        empty = self.tmp / "vacio.avi"
        empty.write_bytes(b"")
        truncated = self.tmp / "truncado.mp4"
        truncated.write_bytes(self.source.read_bytes()[:200])
        for path in (garbage, empty, truncated, self.tmp / "no_existe.mp4"):
            with self.subTest(path=path.name), self.assertRaises(ConversionError):
                video_processor.convert_video(path, 50, "MP4", output_dir=self.out)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])

    def test_invalid_fps_rejected(self):
        fake_capture = mock.MagicMock()
        fake_capture.isOpened.return_value = True
        fake_capture.read.return_value = (True, np.zeros((10, 10, 3), np.uint8))
        fake_capture.get.side_effect = lambda prop: 0.0
        with mock.patch.object(video_processor, "_open_capture", return_value=fake_capture):
            with self.assertRaises(ConversionError) as ctx:
                video_processor.probe_video(self.source)
        self.assertIn("FPS", str(ctx.exception))

    def test_writer_unavailable(self):
        with mock.patch.object(cv2, "VideoWriter") as writer_cls:
            writer_cls.return_value.isOpened.return_value = False
            writer_cls.fourcc = cv2.VideoWriter_fourcc
            with self.assertRaises(ConversionError):
                video_processor._resize_frames(
                    self.source, self.tmp / "x.mp4", "mp4v", video_processor.probe_video(self.source), (160, 120), None, None
                )

    def test_unknown_format_rejected(self):
        with self.assertRaises(ConversionError):
            video_processor.convert_video(self.source, 50, "FLV", output_dir=self.out)

    def test_audio_mux_flow_with_mocked_ffmpeg(self):
        """Verifica la orquestación del audio con un FFmpeg simulado (no es FFmpeg real)."""
        calls = []

        def fake_popen(args):
            calls.append(args)
            process = mock.MagicMock()
            process.returncode = 0
            if "-map" in args:  # mezcla: copia el video reducido como si fuera el resultado con audio
                Path(args[-1]).write_bytes(Path(args[args.index("-i") + 1]).read_bytes())
                process.communicate.return_value = ("", "")
            else:  # inspección del original
                process.communicate.return_value = ("", "Stream #0:1(und): Audio: aac, 44100 Hz")
            return process

        with mock.patch.object(video_processor, "find_ffmpeg", return_value=Path("ffmpeg")), \
                mock.patch.object(ffmpeg_tools, "_popen", side_effect=fake_popen):
            result = video_processor.convert_video(self.source, 50, "MP4", output_dir=self.out)
        self.assertTrue(result.audio_preserved)
        self.assertNotIn(ffmpeg_tools.FFMPEG_MISSING_WARNING, result.warnings)
        mux_call = next(c for c in calls if "-map" in c)
        self.assertEqual(mux_call[mux_call.index("-c:a") + 1], "aac")
        self.assertIn(str(self.source), mux_call)

    def test_audio_mux_failure_falls_back_to_silent(self):
        def fake_popen(args):
            process = mock.MagicMock()
            if "-map" in args:
                process.returncode = 1
                process.communicate.return_value = ("", "error simulado")
            else:
                process.returncode = 0
                process.communicate.return_value = ("", "Stream #0:1: Audio: mp3")
            return process

        with mock.patch.object(video_processor, "find_ffmpeg", return_value=Path("ffmpeg")), \
                mock.patch.object(ffmpeg_tools, "_popen", side_effect=fake_popen):
            result = video_processor.convert_video(self.source, 50, "MP4", output_dir=self.out)
        self.assertFalse(result.audio_preserved)
        self.assertTrue(result.output_path.stat().st_size > 0)
        self.assertTrue(any("audio" in w.lower() for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
