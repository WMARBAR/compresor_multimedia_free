"""Conversor Multimedia: reduce la resolución de imágenes y videos de forma local.

Uso:  python app.py
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from services.common import PROJECT_ROOT, ensure_output_dir


def configure_logging() -> None:
    """Los errores técnicos van a logs/app.log; la interfaz solo muestra mensajes amables."""
    log_dir = PROJECT_ROOT / "logs"
    handlers: list[logging.Handler] = []
    try:
        log_dir.mkdir(exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    configure_logging()
    try:
        from ui.main_window import MainWindow
    except ImportError as exc:
        print(f"Falta una dependencia ({exc.name}). Ejecuta: pip install -r requirements.txt", file=sys.stderr)
        return 1

    ensure_output_dir()
    MainWindow().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
