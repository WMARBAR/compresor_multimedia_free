"""Pruebas de los servicios. Ejecutar:  python -m unittest discover -s tests -v"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ffmpeg_service, video_processor
from services.ffmpeg_service import get_ffmpeg
from services.video_profiles import (
    CRF_GUIDE, CRF_MAX, CRF_MIN, CRF_WARNING_ABOVE, DEFAULT_PROFILE, PROFILES, make_quality,
)
from services.common import (
    OUTPUT_DIR,
    PROJECT_ROOT,
    ConversionCancelled,
    ConversionError,
    calculate_output_size,
    create_batch_folder,
    describe_scale,
    format_bytes,
    format_clock,
    format_elapsed,
    parse_percentage,
    reserve_output_path,
)
from services.compression_stats import (
    CompressionStats,
    format_ratio,
    format_reduction,
    format_saved,
    larger_warning,
)
from services.ffmpeg_service import FFmpegUnavailableError
from services.history import HistoryEntry, SessionHistory
from services.image_processor import (
    available_image_formats,
    convert_image,
    convert_images,
    load_image,
    load_thumbnail,
    parse_image_quality,
)
from services.metadata_service import inspect_image, inspect_video, plan_load


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


BATCH_FOLDER_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?$")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def make_images(self, count, folder=None, size=(200, 120), prefix="frame", ext=".png"):
        folder = folder or self.src
        folder.mkdir(exist_ok=True)
        paths = []
        for index in range(1, count + 1):
            path = folder / f"{prefix}_{index:03d}{ext}"
            make_test_image(path, size[0] + index, size[1] + index)
            paths.append(path)
        return paths

    def snapshot(self, paths):
        return {p: sha256(p) for p in paths}

    def test_one_image_goes_directly_to_outs_without_folder(self):
        (source,) = self.make_images(1)
        result = convert_images([source], 50, "JPG", self.out)
        self.assertFalse(result.used_batch_folder)
        self.assertEqual(result.output_dir, self.out)
        self.assertEqual([p.name for p in self.out.iterdir()], ["frame_001_convertido.jpg"])
        self.assertEqual(len(result.succeeded), 1)

    def test_two_images_create_timestamp_folder(self):
        sources = self.make_images(2)
        before = self.snapshot(sources)
        result = convert_images(sources, 50, "JPG", self.out)
        self.assertTrue(result.used_batch_folder)
        self.assertEqual([p.name for p in self.out.iterdir()], [result.output_dir.name])
        self.assertRegex(result.output_dir.name, BATCH_FOLDER_PATTERN)
        self.assertNotIn(":", result.output_dir.name)
        self.assertEqual(
            sorted(p.name for p in result.output_dir.iterdir()),
            ["frame_001_convertido.jpg", "frame_002_convertido.jpg"],
        )
        self.assertEqual(self.snapshot(sources), before)

    def test_large_batch_progress_and_independent_sizes(self):
        sources = self.make_images(24, size=(300, 200))
        # Resoluciones muy distintas y relaciones de aspecto distintas.
        mixed = self.src / "mixed"
        mixed.mkdir()
        extra = [(mixed / "a.png", (1920, 1080)), (mixed / "b.png", (4000, 3000)), (mixed / "c.png", (500, 1000))]
        for path, (w, h) in extra:
            make_test_image(path, w, h)
        sources += [p for p, _ in extra]
        before = self.snapshot(sources)

        events = []
        result = convert_images(sources, 50, "PNG", self.out, lambda done, total, name: events.append((done, total, name)))

        self.assertEqual(len(result.succeeded), 27)
        self.assertEqual(result.failed, [])
        # Progreso: una notificación por imagen (antes de procesarla) + una final, siempre creciente.
        self.assertEqual([e[0] for e in events], list(range(27)) + [27])
        self.assertTrue(all(total == 27 for _, total, _ in events))
        self.assertEqual([e[2] for e in events[:-1]], [p.name for p in sources])
        self.assertIsNone(events[-1][2])
        # Cada imagen usa su propio tamaño.
        sizes = {r.output_path.name: r.output_size for r in result.succeeded}
        self.assertEqual(sizes["a_convertido.png"], (960, 540))
        self.assertEqual(sizes["b_convertido.png"], (2000, 1500))
        self.assertEqual(sizes["c_convertido.png"], (250, 500))
        for r in result.succeeded:
            decoded = load_image(r.output_path)
            self.assertEqual((decoded.shape[1], decoded.shape[0]), r.output_size)
        self.assertEqual(self.snapshot(sources), before)

    def test_every_output_format(self):
        sources = self.make_images(3)
        for label, ext in [("JPG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp"), ("BMP", ".bmp"), ("TIFF", ".tiff")]:
            with self.subTest(fmt=label):
                result = convert_images(sources, 25, label, self.out)
                self.assertEqual(len(result.succeeded), 3)
                self.assertTrue(all(p.suffix == ext for p in result.output_dir.iterdir()))
                for r, s in zip(result.succeeded, sources):
                    original = load_image(s)
                    self.assertEqual(r.output_size, calculate_output_size(original.shape[1], original.shape[0], 25))

    def test_duplicate_filenames_from_different_directories(self):
        a, b, c = self.src / "a", self.src / "b", self.src / "c"
        for folder, color in ((a, 10), (b, 120), (c, 240)):
            folder.mkdir()
            make_test_image(folder / "imagen.png", 100 + color, 80)
        sources = [a / "imagen.png", b / "imagen.png", c / "imagen.png"]
        before = self.snapshot(sources)
        result = convert_images(sources, 50, "JPG", self.out)
        self.assertEqual(
            sorted(p.name for p in result.output_dir.iterdir()),
            ["imagen_convertido.jpg", "imagen_convertido_1.jpg", "imagen_convertido_2.jpg"],
        )
        # Ninguna salida pisó a otra: cada una conserva el ancho de su origen.
        widths = sorted(load_image(p).shape[1] for p in result.output_dir.iterdir())
        self.assertEqual(widths, [55, 110, 170])  # anchos de origen 110, 220 y 340 al 50 %
        self.assertEqual(self.snapshot(sources), before)

    def test_same_stem_different_extension_does_not_collide(self):
        make_test_image(self.src / "foto.png", 100, 100)
        make_test_image(self.src / "foto.bmp", 120, 120)
        result = convert_images([self.src / "foto.png", self.src / "foto.bmp"], 50, "JPG", self.out)
        self.assertEqual(sorted(p.name for p in result.output_dir.iterdir()), ["foto_convertido.jpg", "foto_convertido_1.jpg"])

    def test_corrupt_image_does_not_stop_the_batch(self):
        sources = self.make_images(5)
        corrupt = sources[2]
        corrupt.write_bytes(b"esto no es una imagen" * 20)
        before = self.snapshot(sources)
        events = []
        result = convert_images(sources, 50, "PNG", self.out, lambda d, t, n: events.append(n))
        self.assertEqual(len(result.succeeded), 4)
        self.assertEqual([p for p, _ in result.failed], [corrupt])
        self.assertTrue(result.failed[0][1])  # motivo legible, no un traceback
        self.assertNotIn("Traceback", result.failed[0][1])
        self.assertEqual(
            sorted(p.name for p in result.output_dir.iterdir()),
            ["frame_001_convertido.png", "frame_002_convertido.png",
             "frame_004_convertido.png", "frame_005_convertido.png"],
        )
        self.assertEqual(events[:5], [p.name for p in sources])  # siguió con las demás
        self.assertEqual(self.snapshot(sources), before)

    def test_all_images_fail_leaves_no_empty_folder(self):
        sources = self.make_images(3)
        for path in sources:
            path.write_bytes(b"x" * 30)
        result = convert_images(sources, 50, "PNG", self.out)
        self.assertEqual(len(result.failed), 3)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_timestamp_folder_format_and_collisions(self):
        moment = datetime(2026, 10, 28, 8, 49, 32)
        first = create_batch_folder(self.out, moment)
        (first / "previo.txt").write_text("no tocar")
        second = create_batch_folder(self.out, moment)
        third = create_batch_folder(self.out, moment)
        self.assertEqual([first.name, second.name, third.name],
                         ["2026-10-28_08-49-32", "2026-10-28_08-49-32_1", "2026-10-28_08-49-32_2"])
        self.assertEqual((first / "previo.txt").read_text(), "no tocar")
        self.assertEqual(list(second.iterdir()), [])

    def test_batch_does_not_overwrite_existing_timestamp_folder(self):
        sources = self.make_images(2)
        moment = datetime(2026, 10, 28, 8, 49, 32)
        existing = self.out / "2026-10-28_08-49-32"
        existing.mkdir(parents=True)
        (existing / "frame_001_convertido.png").write_bytes(b"contenido previo")
        with mock.patch("services.common.datetime") as fake_datetime:
            fake_datetime.now.return_value = moment
            result = convert_images(sources, 50, "PNG", self.out)
        self.assertEqual(result.output_dir.name, "2026-10-28_08-49-32_1")
        self.assertEqual((existing / "frame_001_convertido.png").read_bytes(), b"contenido previo")
        self.assertEqual(len(list(result.output_dir.iterdir())), 2)

    def test_cancel_keeps_converted_images(self):
        sources = self.make_images(6)
        event = threading.Event()
        def cancel_when_second_starts(done, total, name):
            if done == 1:
                event.set()
        result = convert_images(sources, 50, "PNG", self.out, cancel_when_second_starts, event)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result.succeeded), 2)  # la 2.ª ya había empezado; la 3.ª no
        self.assertEqual(len(list(result.output_dir.iterdir())), 2)
        self.assertTrue(all(r.output_path.exists() for r in result.succeeded))

    def test_cancel_before_first_image_removes_empty_folder(self):
        sources = self.make_images(3)
        event = threading.Event()
        event.set()
        result = convert_images(sources, 50, "PNG", self.out, cancel_event=event)
        self.assertTrue(result.cancelled and not result.succeeded)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_invalid_settings_create_nothing(self):
        sources = self.make_images(3)
        for percent, label in ((0, "PNG"), (100, "PNG"), ("abc", "PNG"), (50, "GIF")):
            with self.subTest(percent=percent, fmt=label), self.assertRaises(ConversionError):
                convert_images(sources, percent, label, self.out)
        with self.assertRaises(ConversionError):
            convert_images([], 50, "PNG", self.out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_video_never_uses_a_timestamp_folder(self):
        video = self.tmp / "clip.mp4"
        make_test_video(video, frames=5)
        result = video_processor.convert_video(video, "MP4", output_dir=self.out)
        self.assertEqual(result.output_path.parent, self.out)
        self.assertEqual([p.name for p in self.out.iterdir()], ["clip_convertido.mp4"])


def make_av_video(path: Path, width=640, height=360, seconds=3, fps=30, audio=True,
                  vcodec="libx264", acodec="aac", audio_seconds=None) -> None:
    """Genera un video de prueba con FFmpeg (patrón de colores + tono de 440 Hz si ``audio``)."""
    command = [str(get_ffmpeg().path), "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_seconds or seconds}"]
    command += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", acodec]
    subprocess.run(command + [str(path)], check=True)


def temp_conversion_dirs() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("conv_video_*")}


def video_stats(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return {
            "size": (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
            "fps": fps, "frames": frames, "duration": frames / fps if fps else None,
            "opens": capture.isOpened() and capture.read()[0],
        }
    finally:
        capture.release()


class FFmpegServiceTests(unittest.TestCase):
    def setUp(self):
        ffmpeg_service.reset_ffmpeg_cache()
        self.addCleanup(ffmpeg_service.reset_ffmpeg_cache)

    def test_managed_ffmpeg_is_preferred_and_valid(self):
        with mock.patch.object(ffmpeg_service.shutil, "which", side_effect=AssertionError("no debe consultar el PATH")):
            info = ffmpeg_service.get_ffmpeg()
        self.assertEqual(info.origin, "imageio-ffmpeg")
        self.assertTrue(info.path.is_file())
        self.assertTrue({"libx264", "aac"} <= info.encoders)
        self.assertRegex(info.version, r"^\d")
        print("\nFFmpeg detectado:", info.version, "|", info.origin, "|", info.path)

    def test_detection_is_cached(self):
        first = ffmpeg_service.get_ffmpeg()
        with mock.patch.object(ffmpeg_service, "_candidate_paths", side_effect=AssertionError("redetección")):
            self.assertIs(ffmpeg_service.get_ffmpeg(), first)

    def test_falls_back_to_system_ffmpeg(self):
        real = ffmpeg_service.get_ffmpeg().path
        ffmpeg_service.reset_ffmpeg_cache()
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": mock.MagicMock(get_ffmpeg_exe=mock.Mock(side_effect=RuntimeError("sin binario")))}), \
                mock.patch.object(ffmpeg_service.shutil, "which", return_value=str(real)):
            info = ffmpeg_service.get_ffmpeg()
        self.assertEqual(info.origin, "sistema")

    def test_unavailable_gives_clear_error_and_blocks_formats(self):
        with mock.patch.object(ffmpeg_service, "_candidate_paths", return_value=[]):
            with self.assertRaises(ffmpeg_service.FFmpegUnavailableError) as ctx:
                ffmpeg_service.get_ffmpeg()
            self.assertEqual(str(ctx.exception), ffmpeg_service.FFMPEG_UNAVAILABLE_MESSAGE)
            self.assertEqual(ffmpeg_service.available_profiles(), {})
            self.assertEqual(video_processor.available_video_formats(), {})
            info, message = ffmpeg_service.ffmpeg_status()
            self.assertIsNone(info)
            self.assertIn("requiere FFmpeg", message)

    def test_unusable_executable_is_rejected(self):
        with mock.patch.object(ffmpeg_service, "_candidate_paths", return_value=[("sistema", Path("no_existe_ffmpeg.exe"))]):
            self.assertIsNone(ffmpeg_service.ffmpeg_status()[0])

    def test_no_shell_true_anywhere(self):
        for module in (ffmpeg_service, video_processor):
            self.assertNotIn("shell=True", Path(module.__file__).read_text(encoding="utf-8"))

    def test_probe_parsing(self):
        stderr = (
            "Input #0, mov,mp4, from 'x.mp4':\n  Duration: 00:01:02.50, start: 0.000000, bitrate: 900 kb/s\n"
            "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 960x540 [SAR 1:1 DAR 16:9], 30 fps\n"
            "  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s\n"
        )
        info = ffmpeg_service.get_ffmpeg()
        with mock.patch.object(ffmpeg_service, "_run", return_value=mock.Mock(stderr=stderr, returncode=1)):
            probe = ffmpeg_service.probe_media(Path("x.mp4"), info)
        self.assertEqual((probe.video_codec, probe.pixel_format, probe.audio_codec), ("h264", "yuv420p", "aac"))
        self.assertAlmostEqual(probe.duration_seconds, 62.5)


class VideoProfileTests(unittest.TestCase):
    def test_default_profile_keeps_original_resolution(self):
        self.assertEqual(DEFAULT_PROFILE, "high")
        quality = make_quality()
        self.assertEqual((quality.label, quality.crf), ("Alta calidad", 20))
        self.assertEqual(quality.target_size(1920, 1080), (1920, 1080))
        self.assertEqual(quality.quality_text, "Alta (CRF 20)")

    def test_profile_settings(self):
        max_q, high_q, web_q, comp_q = (make_quality(k) for k in ("max", "high", "web", "compression"))
        self.assertEqual((max_q.crf, max_q.preset), (18, "slow"))
        self.assertEqual((high_q.crf, high_q.preset), (20, "medium"))
        self.assertEqual((web_q.crf, web_q.preset), (20, "medium"))
        self.assertEqual((comp_q.crf, comp_q.preset), (23, "medium"))
        self.assertEqual(max_q.target_size(1920, 1080), (1920, 1080))
        self.assertEqual(comp_q.target_size(1920, 1080), (960, 540))
        self.assertIn("nitidez", PROFILES["compression"].warning)

    def test_web_profile_never_upscales_and_keeps_aspect_ratio(self):
        web = make_quality("web")
        for source, expected in [
            ((1920, 1080), (1280, 720)), ((3840, 2160), (1280, 720)), ((1280, 720), (1280, 720)),
            ((854, 480), (854, 480)), ((640, 360), (640, 360)), ((2560, 1080), (1280, 540)),
            ((1080, 1920), (720, 1280)), ((1080, 1080), (720, 720)), ((1366, 768), (1280, 720)),
        ]:
            with self.subTest(source=source):
                self.assertEqual(web.target_size(*source), expected)
                self.assertLessEqual(expected[0], source[0])
                self.assertLessEqual(expected[1], source[1])

    def test_target_size_is_always_even_and_never_larger(self):
        for key in ("max", "high", "web", "compression"):
            for source in ((1921, 1081), (641, 361), (853, 479), (1920, 1080)):
                width, height = make_quality(key).target_size(*source)
                self.assertTrue(width % 2 == 0 and height % 2 == 0, (key, source))
                self.assertTrue(width <= source[0] and height <= source[1], (key, source))

    def test_custom_profile(self):
        self.assertEqual(make_quality("custom", 0, 20).target_size(1920, 1080), (1920, 1080))
        self.assertEqual(make_quality("custom", 25, 21).target_size(1920, 1080), (1440, 810))
        self.assertEqual(make_quality("custom", "33,5", "23 — Mayor compresión").crf, 23)
        self.assertEqual(make_quality("custom", 0, "28").crf, 28)

    def test_invalid_custom_values_are_rejected(self):
        for crf in (16, 29, 0, -5, "abc", "", "nan", "20.5"):
            with self.subTest(crf=crf), self.assertRaises(ConversionError):
                make_quality("custom", 0, crf)
        for reduction in (-1, 100, 150, "abc", "", "nan"):
            with self.subTest(reduction=reduction), self.assertRaises(ConversionError):
                make_quality("custom", reduction, 20)
        with self.assertRaises(ConversionError):
            make_quality("inexistente")

    def test_crf_guidance_labels(self):
        self.assertEqual({18: "Muy alta calidad", 20: "Alta calidad", 21: "Buena calidad", 23: "Mayor compresión"}, CRF_GUIDE)
        self.assertEqual((CRF_MIN, CRF_MAX, CRF_WARNING_ABOVE), (17, 28, 23))

    def test_format_bytes(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(3120104), "3.0 MB")
        self.assertEqual(format_bytes(15_517_000), "14.8 MB")


class VideoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.addCleanup(self._tmp.cleanup)
        self.source = self.tmp / "clip.mp4"
        make_av_video(self.source)
        self.ffmpeg = get_ffmpeg()
        self._temp_before = temp_conversion_dirs()

    def convert(self, source=None, quality=None, fmt="MP4", **kwargs):
        return video_processor.convert_video(source or self.source, fmt, quality, output_dir=self.out, **kwargs)

    def assertNoTempLeftovers(self):
        self.assertEqual(temp_conversion_dirs() - self._temp_before, set(), "quedaron temporales de la conversión")

    def assertFinalIsWebH264(self, path: Path, audio: bool):
        probe = ffmpeg_service.probe_media(path, self.ffmpeg)
        self.assertEqual(probe.video_codec, "h264")  # nunca mpeg4 (MPEG-4 Parte 2)
        self.assertEqual(probe.pixel_format, "yuv420p")
        self.assertEqual(probe.audio_codec, "aac" if audio else None)
        return probe

    # -- metadatos y formatos ------------------------------------------------------------
    def test_probe_detects_audio_and_no_audio(self):
        info = video_processor.probe_video(self.source)
        self.assertEqual((info.width, info.height, info.frame_count), (640, 360, 90))
        self.assertAlmostEqual(info.fps, 30.0, places=1)
        self.assertTrue(info.has_audio)
        self.assertAlmostEqual(info.container_duration, 3.0, delta=0.1)
        silent = self.tmp / "mudo.mp4"
        make_av_video(silent, audio=False)
        self.assertFalse(video_processor.probe_video(silent).has_audio)

    def test_available_formats_are_h264_containers_only(self):
        self.assertEqual(video_processor.available_video_formats(), {"MP4": ".mp4", "MOV": ".mov"})

    # -- perfiles -------------------------------------------------------------------------
    def test_default_quality_keeps_1080p_and_everything_else(self):
        big = self.tmp / "grande.mp4"
        make_av_video(big, 1920, 1080, seconds=3)
        before = sha256(big)
        result = self.convert(big)  # sin perfil: «Alta calidad»
        self.assertEqual(result.output_path.name, "grande_convertido.mp4")
        self.assertEqual(result.output_size, (1920, 1080))
        self.assertEqual(result.source_size, (1920, 1080))
        self.assertEqual(result.quality_label, "Alta (CRF 20)")
        self.assertEqual((result.video_codec, result.audio_codec), ("H.264", "AAC"))
        self.assertTrue(result.has_source_audio and result.audio_preserved)
        self.assertEqual(result.source_bytes, big.stat().st_size)
        self.assertFinalIsWebH264(result.output_path, audio=True)
        stats = video_stats(result.output_path)
        self.assertTrue(stats["opens"])
        self.assertEqual(stats["size"], (1920, 1080))
        self.assertAlmostEqual(stats["fps"], 30.0, places=1)
        self.assertAlmostEqual(stats["duration"], 3.0, delta=0.2)
        data = result.output_path.read_bytes()
        self.assertLess(data.index(b"moov"), data.index(b"mdat"), "faststart: moov debe ir antes de mdat")
        self.assertEqual(sha256(big), before)
        self.assertNoTempLeftovers()
        self.assertEqual([p.name for p in self.out.iterdir()], ["grande_convertido.mp4"])

    def test_each_profile_on_1080p(self):
        big = self.tmp / "grande.mp4"
        make_av_video(big, 1920, 1080, seconds=3)
        for key, expected in (("max", (1920, 1080)), ("high", (1920, 1080)), ("web", (1280, 720)),
                              ("compression", (960, 540))):
            with self.subTest(profile=key):
                result = self.convert(big, make_quality(key))
                self.assertEqual(result.output_size, expected)
                self.assertEqual(video_stats(result.output_path)["size"], expected)
                self.assertTrue(result.quality_label.endswith(f"(CRF {make_quality(key).crf})"))
                self.assertFinalIsWebH264(result.output_path, audio=True)

    def test_web_profile_does_not_upscale_small_videos(self):
        for size in ((1280, 720), (854, 480)):
            with self.subTest(size=size):
                source = self.tmp / f"v{size[0]}.mp4"
                make_av_video(source, *size, seconds=1)
                result = self.convert(source, make_quality("web"))
                self.assertEqual(result.output_size, size)
                self.assertEqual(video_stats(result.output_path)["size"], size)

    def test_custom_percentages_and_crf(self):
        for reduction, expected in ((0, (640, 360)), (25, (480, 270)), (50, (320, 180)), (75, (160, 90))):
            with self.subTest(reduction=reduction):
                result = self.convert(quality=make_quality("custom", reduction, 21))
                self.assertEqual(result.output_size, expected)
                self.assertEqual(video_stats(result.output_path)["size"], expected)
                self.assertEqual(result.quality_label, "Personalizado (CRF 21)")

    def test_lower_crf_means_larger_file(self):
        sizes = {crf: self.convert(quality=make_quality("custom", 0, crf)).output_path.stat().st_size for crf in (18, 23, 28)}
        self.assertGreater(sizes[18], sizes[23])
        self.assertGreater(sizes[23], sizes[28])

    def test_transcode_command_lanczos_only_when_resizing(self):
        info = ffmpeg_service.get_ffmpeg()
        profile = ffmpeg_service.VIDEO_PROFILES["MP4"]
        high = make_quality("high")
        plain = ffmpeg_service.build_transcode_command(info, profile, high, Path("a.mp4"), Path("o.mp4"), None, True)
        self.assertNotIn("-vf", plain)  # sin reducción no se toca ni un píxel
        scaled = ffmpeg_service.build_transcode_command(
            info, profile, make_quality("web"), Path("a.mp4"), Path("o.mp4"), (1280, 720), True)
        self.assertEqual(scaled[scaled.index("-vf") + 1], "scale=1280:720:flags=lanczos")
        for command in (plain, scaled):
            pairs = dict(zip(command, command[1:]))
            self.assertIsInstance(command, list)
            self.assertEqual(pairs["-c:v"], "libx264")
            self.assertEqual(pairs["-c:a"], "aac")
            self.assertEqual(pairs["-pix_fmt"], "yuv420p")
            self.assertEqual(pairs["-movflags"], "+faststart")
            self.assertEqual(pairs["-crf"], "20")
            self.assertEqual(command.count("-i"), 1)  # una sola entrada: el video original
            self.assertNotIn("rawvideo", command)
            self.assertIn("0:a:0", command)
        silent = ffmpeg_service.build_transcode_command(info, profile, high, Path("a.mp4"), Path("o.mp4"), None, False)
        self.assertIn("-an", silent)
        self.assertNotIn("0:a:0", silent)
        max_cmd = ffmpeg_service.build_transcode_command(info, profile, make_quality("max"), Path("a.mp4"), Path("o.mp4"), None, True)
        self.assertEqual(dict(zip(max_cmd, max_cmd[1:]))["-preset"], "slow")

    def test_mov_uses_h264_and_aac(self):
        result = self.convert(fmt="MOV")
        self.assertEqual(result.output_path.suffix, ".mov")
        self.assertFinalIsWebH264(result.output_path, audio=True)

    # -- audio ----------------------------------------------------------------------------
    def test_source_without_audio_is_not_an_error(self):
        silent = self.tmp / "mudo.mp4"
        make_av_video(silent, audio=False)
        result = self.convert(silent)
        self.assertFalse(result.has_source_audio)
        self.assertFalse(result.audio_preserved)
        self.assertIsNone(result.audio_codec)
        self.assertEqual(result.warnings, [])
        self.assertFinalIsWebH264(result.output_path, audio=False)

    def test_audio_longer_or_shorter_than_video_is_preserved_in_sync(self):
        for name, audio_seconds in (("corto", 1), ("largo", 8)):
            with self.subTest(audio=name):
                source = self.tmp / f"audio_{name}.mp4"
                make_av_video(source, seconds=3, audio_seconds=audio_seconds)
                result = self.convert(source)
                self.assertTrue(result.audio_preserved)
                self.assertAlmostEqual(
                    ffmpeg_service.probe_media(result.output_path).duration_seconds,
                    ffmpeg_service.probe_media(source).duration_seconds, delta=0.3)

    # -- entradas -------------------------------------------------------------------------
    def test_legacy_mpeg4_source_becomes_h264(self):
        legacy = self.tmp / "viejo.mp4"
        make_test_video(legacy, 320, 240, frames=20)  # mp4v, sin audio (lo que generaba la versión original)
        self.assertEqual(ffmpeg_service.probe_media(legacy, self.ffmpeg).video_codec, "mpeg4")
        result = self.convert(legacy)
        self.assertEqual(result.output_size, (320, 240))
        self.assertFinalIsWebH264(result.output_path, audio=False)

    def test_other_input_containers(self):
        mkv, avi = self.tmp / "a.mkv", self.tmp / "b.avi"
        make_av_video(mkv, seconds=2)
        make_av_video(avi, seconds=2, vcodec="mpeg4", acodec="libmp3lame")
        for source in (mkv, avi):
            with self.subTest(source=source.name):
                result = self.convert(source)
                self.assertTrue(result.audio_preserved)
                self.assertFinalIsWebH264(result.output_path, audio=True)

    def test_odd_dimensions_become_even_without_distortion(self):
        odd = self.tmp / "odd.mp4"
        make_test_video(odd, 321, 241, frames=10)
        result = self.convert(odd)
        self.assertEqual(result.output_size, (320, 240))
        self.assertEqual(video_stats(result.output_path)["size"], (320, 240))

    # -- comportamiento general -----------------------------------------------------------
    def test_duplicate_output_names(self):
        names = [self.convert().output_path.name for _ in range(3)]
        self.assertEqual(names, ["clip_convertido.mp4", "clip_convertido_1.mp4", "clip_convertido_2.mp4"])

    def test_progress_stages_in_order(self):
        events = []
        self.convert(progress_callback=lambda s, d, t: events.append((s, d, t)))
        stages = [e[0] for e in events]
        self.assertEqual([s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]],
                         ["preparing", "encoding", "validating"])
        encoding = [e for e in events if e[0] == "encoding"]
        self.assertEqual(encoding[-1], ("encoding", 90, 90))
        self.assertEqual([e[1] for e in encoding], sorted(e[1] for e in encoding))

    def test_opencv_never_encodes_or_resizes_frames(self):
        with mock.patch.object(cv2, "VideoWriter", side_effect=AssertionError("VideoWriter no debe usarse")), \
                mock.patch.object(cv2, "resize", side_effect=AssertionError("cv2.resize no debe usarse")):
            result = self.convert(quality=make_quality("compression"))
        self.assertEqual(result.output_size, (320, 180))
        self.assertFinalIsWebH264(result.output_path, audio=True)

    # -- fallos ---------------------------------------------------------------------------
    def test_ffmpeg_failure_leaves_nothing_behind(self):
        broken = dataclasses.replace(ffmpeg_service.VIDEO_PROFILES["MP4"], video_encoder="encoder_que_no_existe")
        before = sha256(self.source)
        with mock.patch.object(video_processor, "available_profiles", return_value={"MP4": broken}):
            with self.assertRaises(ConversionError) as ctx:
                self.convert()
        self.assertIn("H.264", str(ctx.exception))
        self.assertNotIn("Traceback", str(ctx.exception))
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertNoTempLeftovers()
        self.assertEqual(sha256(self.source), before)

    def test_failed_validation_discards_output(self):
        real_probe = video_processor.probe_media

        def fake_probe(path, ffmpeg=None):
            if Path(path).name.startswith("final"):  # el archivo generado "resulta" ser MPEG-4 Parte 2
                return ffmpeg_service.MediaProbe("mpeg4", "yuv420p", "aac", 3.0)
            return real_probe(path) if ffmpeg is None else real_probe(path, ffmpeg)

        with mock.patch.object(video_processor, "probe_media", side_effect=fake_probe):
            with self.assertRaises(ConversionError) as ctx:
                self.convert()
        self.assertIn("validación", str(ctx.exception))
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertNoTempLeftovers()

    def test_missing_audio_in_output_is_a_validation_error(self):
        real_probe = video_processor.probe_media

        def fake_probe(path, ffmpeg=None):
            probe = real_probe(path) if ffmpeg is None else real_probe(path, ffmpeg)
            if Path(path).name.startswith("final"):
                return ffmpeg_service.MediaProbe(probe.video_codec, probe.pixel_format, None, probe.duration_seconds)
            return probe

        with mock.patch.object(video_processor, "probe_media", side_effect=fake_probe):
            with self.assertRaises(ConversionError):
                self.convert()
        self.assertEqual(list(self.out.iterdir()), [])

    def test_ffmpeg_unavailable_blocks_conversion_without_silent_fallback(self):
        ffmpeg_service.reset_ffmpeg_cache()
        self.addCleanup(ffmpeg_service.reset_ffmpeg_cache)
        with mock.patch.object(ffmpeg_service, "_candidate_paths", return_value=[]):
            with self.assertRaises(ffmpeg_service.FFmpegUnavailableError):
                self.convert()
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))
        self.assertNoTempLeftovers()

    def test_cancel_terminates_ffmpeg_and_cleans_up(self):
        long_source = self.tmp / "larga.mp4"
        make_av_video(long_source, 1920, 1080, seconds=8)
        started = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            if "-progress" in args[0]:  # solo la conversión (no las consultas de versión/sondeo)
                started.append(process)
            return process

        event = threading.Event()
        before = sha256(long_source)
        with mock.patch.object(ffmpeg_service.subprocess, "Popen", side_effect=tracking_popen):
            with self.assertRaises(ConversionCancelled):
                self.convert(long_source, make_quality("max"),
                             progress_callback=lambda s, d, t: event.set() if s == "encoding" and d >= 2 else None,
                             cancel_event=event)
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll(), "ffmpeg sigue en ejecución")
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertNoTempLeftovers()
        self.assertEqual(sha256(long_source), before)

    def test_cancel_before_start(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(ConversionCancelled):
            self.convert(cancel_event=event)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])
        self.assertNoTempLeftovers()

    def test_corrupt_and_invalid_videos(self):
        garbage = self.tmp / "roto.mp4"
        garbage.write_bytes(b"no soy un video" * 100)
        empty = self.tmp / "vacio.avi"
        empty.write_bytes(b"")
        truncated = self.tmp / "truncado.mp4"
        truncated.write_bytes(self.source.read_bytes()[:200])
        for path in (garbage, empty, truncated, self.tmp / "no_existe.mp4"):
            with self.subTest(path=path.name), self.assertRaises(ConversionError):
                self.convert(path)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])
        self.assertNoTempLeftovers()

    def test_invalid_fps_rejected(self):
        fake_capture = mock.MagicMock()
        fake_capture.isOpened.return_value = True
        fake_capture.read.return_value = (True, np.zeros((10, 10, 3), np.uint8))
        fake_capture.get.side_effect = lambda prop: 0.0
        with mock.patch.object(video_processor, "_open_capture", return_value=fake_capture):
            with self.assertRaises(ConversionError) as ctx:
                video_processor.probe_video(self.source)
        self.assertIn("FPS", str(ctx.exception))

    def test_unknown_or_unsupported_format_rejected(self):
        for label in ("FLV", "AVI"):
            with self.subTest(fmt=label), self.assertRaises(ConversionError):
                self.convert(fmt=label)


# ===================================================================== dashboard pass ==
class CompressionStatsTests(unittest.TestCase):
    MB = 1024 * 1024

    def test_example_from_spec(self):
        stats = CompressionStats(int(42.6 * self.MB), int(14.8 * self.MB))
        self.assertAlmostEqual(stats.reduction_percent, 65.26, places=1)
        self.assertAlmostEqual(stats.ratio, 2.88, places=2)
        self.assertEqual(format_ratio(stats), "2.88 : 1")
        self.assertEqual(format_reduction(stats), "65.3%")
        self.assertEqual(format_saved(stats), "27.8 MB")
        self.assertFalse(stats.is_larger)
        self.assertIsNone(larger_warning(stats))

    def test_ratio_is_not_the_percentage(self):
        stats = CompressionStats(1000, 500)
        self.assertEqual((stats.ratio, stats.reduction_percent), (2.0, 50.0))

    def test_larger_result_is_reported_honestly(self):
        stats = CompressionStats(1000, 1084)
        self.assertTrue(stats.is_larger)
        self.assertEqual(stats.saved, -84)
        self.assertLess(stats.reduction_percent, 0)
        self.assertEqual(larger_warning(stats), "El archivo resultante es 8.4% más grande que el original.")
        self.assertTrue(format_saved(stats).startswith("+"))

    def test_degenerate_sizes_do_not_crash(self):
        self.assertIsNone(CompressionStats(100, 0).ratio)
        self.assertEqual(format_ratio(CompressionStats(100, 0)), "—")
        self.assertEqual(CompressionStats(0, 0).reduction_percent, 0.0)
        self.assertEqual(format_saved(CompressionStats(10, 10)), "0 B")


class FormatHelperTests(unittest.TestCase):
    def test_elapsed_and_clock(self):
        self.assertEqual(format_elapsed(18.4), "18.4 s")
        self.assertEqual(format_elapsed(102), "01:42")
        self.assertEqual(format_elapsed(3725), "01:02:05")
        self.assertEqual(format_elapsed(None), "—")
        self.assertEqual(format_clock(18.4), "00:00:18")
        self.assertEqual(format_clock(3725), "01:02:05")

    def test_describe_scale(self):
        linear, pixels = describe_scale((1920, 1080), (1280, 720))
        self.assertAlmostEqual(linear, 66.67, places=1)
        self.assertAlmostEqual(pixels, 44.44, places=1)
        self.assertEqual(describe_scale((1920, 1080), (1920, 1080)), (100.0, 100.0))


class HistoryTests(unittest.TestCase):
    def test_newest_first_and_bounded(self):
        history = SessionHistory(limit=3)
        for number in range(5):
            history.add(HistoryEntry(f"f{number}.mp4", "video", 10, 5, Path(f"f{number}.mp4")))
        self.assertEqual([entry.name for entry in history.entries], ["f4.mp4", "f3.mp4", "f2.mp4"])
        self.assertEqual(len(history), 3)

    def test_remove_discarded_location(self):
        history = SessionHistory()
        history.add(HistoryEntry("a.png", "imagen", 10, 5, Path("outs/a.png")))
        history.add(HistoryEntry("b.png", "imagen", 10, 5, Path("outs/b.png")))
        history.remove_location(Path("outs/a.png"))
        self.assertEqual([entry.name for entry in history.entries], ["b.png"])


class ResultFieldsTests(unittest.TestCase):
    """Los resultados traen tamaños y tiempos medidos sobre los archivos reales."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "outs"
        self.addCleanup(self._tmp.cleanup)

    def test_image_result_fields(self):
        source = self.tmp / "foto.png"
        make_test_image(source, 800, 600)
        result = convert_image(source, 50, "JPG", self.out)
        self.assertEqual(result.kind, "imagen")
        self.assertEqual(result.source_size, (800, 600))
        self.assertEqual(result.source_bytes, source.stat().st_size)
        self.assertEqual(result.output_bytes, result.output_path.stat().st_size)
        self.assertEqual((result.source_format, result.format_label), ("PNG", "JPG"))
        self.assertGreaterEqual(result.elapsed_seconds, 0)
        self.assertEqual(result.source_path, source)

    def test_video_result_fields(self):
        source = self.tmp / "clip.mp4"
        make_av_video(source)
        result = video_processor.convert_video(source, "MP4", make_quality("web"), output_dir=self.out)
        self.assertEqual(result.kind, "video")
        self.assertEqual(result.output_bytes, result.output_path.stat().st_size)
        self.assertEqual(result.source_bytes, source.stat().st_size)
        self.assertGreater(result.elapsed_seconds, 0)
        self.assertEqual((result.source_video_codec, result.video_codec), ("H.264", "H.264"))
        self.assertEqual((result.source_audio_codec, result.audio_codec), ("AAC", "AAC"))

    def test_batch_totals_and_elapsed(self):
        sources = []
        for number in range(3):
            path = self.tmp / f"i{number}.png"
            make_test_image(path, 400 + number * 100, 300)
            sources.append(path)
        (self.tmp / "rota.png").write_bytes(b"no es imagen")
        sources.append(self.tmp / "rota.png")
        batch = convert_images(sources, 50, "JPG", self.out)
        self.assertEqual((len(batch.succeeded), len(batch.failed)), (3, 1))
        self.assertEqual(batch.source_bytes_total, sum(p.stat().st_size for p in sources[:3]))
        self.assertEqual(batch.output_bytes_total, sum(item.output_path.stat().st_size for item in batch.succeeded))
        self.assertGreater(batch.elapsed_seconds, 0)

    def test_image_quality_changes_lossy_size_only(self):
        source = self.tmp / "ruido.png"
        noise = (np.random.default_rng(7).random((300, 400, 3)) * 255).astype(np.uint8)
        cv2.imwrite(str(source), noise)
        low = convert_image(source, 1, "JPG", self.out, image_quality=30).output_bytes
        high = convert_image(source, 1, "JPG", self.out, image_quality=95).output_bytes
        self.assertLess(low, high)
        webp_low = convert_image(source, 1, "WEBP", self.out, image_quality=30).output_bytes
        webp_high = convert_image(source, 1, "WEBP", self.out, image_quality=95).output_bytes
        self.assertLess(webp_low, webp_high)
        png_a = convert_image(source, 1, "PNG", self.out, image_quality=10).output_bytes
        png_b = convert_image(source, 1, "PNG", self.out).output_bytes
        self.assertEqual(png_a, png_b)  # PNG es sin pérdida: la calidad no aplica

    def test_invalid_image_quality(self):
        for value in ("0", "101", "abc", "", "-3", "9.5"):
            with self.subTest(value=value), self.assertRaises(ConversionError):
                parse_image_quality(value)
        self.assertEqual(parse_image_quality(" 80 "), 80)

    def test_thumbnail_is_small_and_keeps_aspect(self):
        source = self.tmp / "grande.png"
        make_test_image(source, 4000, 1000)
        thumbnail = load_thumbnail(source, (400, 250))
        self.assertLessEqual(thumbnail.width, 400)
        self.assertLessEqual(thumbnail.height, 250)
        self.assertAlmostEqual(thumbnail.width / thumbnail.height, 4.0, delta=0.1)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        ffmpeg_service.reset_ffmpeg_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def rows(self, meta) -> dict[str, str]:
        return dict(meta.rows())

    def test_image_metadata_only_detected_values(self):
        opaque, alpha = self.tmp / "a.png", self.tmp / "b.png"
        make_test_image(opaque, 320, 200)
        make_test_image(alpha, 320, 200, alpha=True)
        meta = inspect_image(opaque)
        rows = self.rows(meta)
        self.assertEqual((rows["Archivo"], rows["Resolución"], rows["Formato"]), ("a.png", "320 × 200 px", "PNG"))
        self.assertEqual((rows["Transparencia"], meta.channels), ("No", 3))
        self.assertEqual(self.rows(inspect_image(alpha))["Transparencia"], "Sí (canal alfa)")
        self.assertNotIn("FPS", rows)
        self.assertNotIn("Duración", rows)

    def test_image_format_comes_from_content_not_extension(self):
        disguised = self.tmp / "foto.png"
        make_test_image(self.tmp / "real.jpg", 100, 80)
        disguised.write_bytes((self.tmp / "real.jpg").read_bytes())
        self.assertEqual(inspect_image(disguised).container, "JPG")

    def test_video_metadata_with_and_without_audio(self):
        with_audio, silent = self.tmp / "audio.mp4", self.tmp / "mudo.mp4"
        make_av_video(with_audio, 640, 360, 2, 30, True)
        make_av_video(silent, 640, 360, 2, 30, False)
        meta = inspect_video(with_audio)
        rows = self.rows(meta)
        self.assertEqual((rows["Códec video"], rows["Audio"], rows["FPS"]), ("H.264", "AAC", "30"))
        self.assertEqual(rows["Resolución"], "640 × 360 px")
        self.assertEqual(rows["Contenedor"], "MP4")
        self.assertEqual(self.rows(inspect_video(silent))["Audio"], "NINGUNO")  # sin audio no es un error

    def test_video_without_ffmpeg_does_not_invent_codecs(self):
        video = self.tmp / "v.mp4"
        make_av_video(video, 320, 240, 1, 30, True)
        with mock.patch.object(video_processor, "probe_media", side_effect=FFmpegUnavailableError("sin ffmpeg")):
            meta = inspect_video(video)
        rows = self.rows(meta)
        for missing in ("Códec video", "Audio"):
            self.assertNotIn(missing, rows)
        self.assertEqual(rows["Resolución"], "320 × 240 px")

    def test_unreliable_frame_count_is_hidden(self):
        video = self.tmp / "v.mp4"
        make_av_video(video, 320, 240, 2, 30, False)
        info = video_processor.probe_video(video)
        wrong = dataclasses.replace(info, frame_count=info.frame_count * 3)
        with mock.patch("services.metadata_service.probe_video", return_value=wrong):
            self.assertIsNone(inspect_video(video).frame_count)
        self.assertEqual(inspect_video(video).frame_count, info.frame_count)

    def test_corrupt_files_raise_friendly_errors(self):
        broken_image, broken_video = self.tmp / "x.png", self.tmp / "x.mp4"
        broken_image.write_bytes(b"nada")
        broken_video.write_bytes(b"nada")
        for inspector, path in ((inspect_image, broken_image), (inspect_video, broken_video)):
            with self.subTest(path=path.name), self.assertRaises(ConversionError):
                inspector(path)

    def test_plan_load_rules(self):
        files = {name: self.tmp / name for name in ("a.png", "b.JPG", "c.mp4", "d.mov", "e.txt")}
        for path in files.values():
            path.write_bytes(b"x")
        plan = plan_load([files["a.png"], files["b.JPG"]])
        self.assertEqual((plan.kind, len(plan.files)), ("imagen", 2))
        plan = plan_load([files["c.mp4"], files["d.mov"]])  # un video a la vez
        self.assertEqual((plan.kind, plan.files), ("video", [files["c.mp4"]]))
        self.assertTrue(any("un video a la vez" in warning for warning in plan.warnings))
        plan = plan_load([files["a.png"], files["e.txt"]])  # lo no compatible se ignora y se avisa
        self.assertEqual(len(plan.files), 1)
        self.assertTrue(any("e.txt" in warning for warning in plan.warnings))
        for bad in ([files["a.png"], files["c.mp4"]], [files["e.txt"]], [self.tmp], []):
            with self.subTest(bad=[p.name for p in bad]), self.assertRaises(ConversionError):
                plan_load(bad)


class AdvancedVideoSettingsTests(unittest.TestCase):
    def test_limits_never_upscale_and_keep_aspect(self):
        quality = make_quality("custom", 0, 21, "slow", "1280", "")
        self.assertEqual(quality.target_size(1920, 1080), (1280, 720))
        self.assertEqual(quality.target_size(640, 360), (640, 360))  # nunca amplía
        both = make_quality("custom", 0, 21, "medium", "1000", "400")
        width, height = both.target_size(1920, 1080)
        self.assertLessEqual(width, 1000)
        self.assertLessEqual(height, 400)
        self.assertAlmostEqual(width / height, 16 / 9, delta=0.05)  # sin estirar
        self.assertTrue(width % 2 == 0 and height % 2 == 0)
        combined = make_quality("custom", 25, 21, "medium", "1000", "")
        self.assertLessEqual(combined.target_size(1920, 1080)[0], 1000)  # reducción y límite se combinan

    def test_preset_and_dimensions_validated(self):
        self.assertEqual(make_quality("custom", 0, 20, "slower").preset, "slower")
        for kwargs in ({"custom_preset": "ultrafast"}, {"max_width": "abc"}, {"max_width": "10"},
                       {"max_height": "99999"}, {"max_width": "-5"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConversionError):
                make_quality("custom", 0, 20, **kwargs)

    def test_fixed_profiles_ignore_advanced_values(self):
        self.assertEqual(make_quality("high", 0, 20, "slower", "320", "240").target_size(1920, 1080), (1920, 1080))
        self.assertEqual(make_quality("high").preset, "medium")

    def test_custom_preset_reaches_ffmpeg_command(self):
        info = get_ffmpeg()
        profile = ffmpeg_service.VIDEO_PROFILES["MP4"]
        command = ffmpeg_service.build_transcode_command(
            info, profile, make_quality("custom", 0, 24, "fast"), Path("a.mp4"), Path("o.mp4"), None, True)
        self.assertEqual(command[command.index("-preset") + 1], "fast")
        self.assertEqual(command[command.index("-crf") + 1], "24")
        self.assertNotIn("-vf", command)

    def test_custom_limits_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "v.mp4"
            make_av_video(source, 1280, 720, 2, 30, True)
            result = video_processor.convert_video(
                source, "MP4", make_quality("custom", 0, 26, "veryfast", "640", ""), output_dir=Path(tmp) / "outs")
            self.assertEqual(result.output_size, (640, 360))
            self.assertEqual(video_stats(result.output_path)["size"], (640, 360))


if __name__ == "__main__":
    unittest.main()
