"""Arrastrar y soltar archivos desde el Explorador de Windows, sin dependencias externas.

Se registra la ventana como destino de archivos (``DragAcceptFiles``) y se intercepta
``WM_DROPFILES`` con un *subclass* de ventana (``comctl32``), todo con ``ctypes``. En otros
sistemas, o si algo falla, ``install()`` devuelve ``False`` y la aplicación sigue funcionando
con el botón de selección: el arrastre es una comodidad, nunca un requisito.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

WM_DROPFILES = 0x0233
_WM_COPYGLOBALDATA = 0x0049
_MSGFLT_ALLOW = 1


class FileDropTarget:
    """Recibe rutas soltadas sobre la ventana y las entrega al hilo de la GUI mediante ``after``.

    El procedimiento de ventana de Win32 se ejecuta *dentro* de una llamada a Tcl, así que no puede
    usar Tk (corrompería el estado de hilos de ``_tkinter``): solo encola las rutas, y un hilo
    auxiliar las entrega con ``after`` (Tkinter las traslada al hilo principal; requiere ``mainloop``).
    """

    def __init__(self, window: tk.Misc, callback: Callable[[list[Path]], None]) -> None:
        self._window = window
        self._callback = callback
        self._pending: queue.Queue[list[Path] | None] = queue.Queue()
        self._deliverer: threading.Thread | None = None
        self._installed: list[int] = []
        self._proc = None  # referencia viva al callback de ctypes (si se recoge, la app se cierra con error)
        self._comctl32 = None

    @staticmethod
    def supported() -> bool:
        return sys.platform.startswith("win")

    def install(self) -> bool:
        if not self.supported():
            return False
        if self._installed:  # instalar dos veces dejaría un callback de ctypes liberado enganchado a la ventana
            return True
        try:
            import ctypes
            from ctypes import wintypes

            user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
            comctl32 = ctypes.WinDLL("comctl32")

            lresult = ctypes.c_ssize_t
            subclass_proc = ctypes.WINFUNCTYPE(
                lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                ctypes.c_size_t, ctypes.c_size_t,
            )
            comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, subclass_proc, ctypes.c_size_t, ctypes.c_size_t]
            comctl32.SetWindowSubclass.restype = wintypes.BOOL
            comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, subclass_proc, ctypes.c_size_t]
            comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            comctl32.DefSubclassProc.restype = lresult
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [ctypes.c_void_p]
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]

            def handle_drop(hdrop: int) -> list[Path]:
                count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                paths: list[Path] = []
                for index in range(count):
                    length = shell32.DragQueryFileW(hdrop, index, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
                    paths.append(Path(buffer.value))
                shell32.DragFinish(hdrop)
                return paths

            def window_proc(hwnd, message, wparam, lparam, _subclass_id, _data):
                if message == WM_DROPFILES:
                    try:
                        self._pending.put(handle_drop(wparam))  # sin tocar Tk aquí
                    except Exception:
                        logger.exception("Error procesando archivos soltados")
                    return 0
                return comctl32.DefSubclassProc(hwnd, message, wparam, lparam)

            self._proc = subclass_proc(window_proc)
            self._comctl32 = comctl32
            self._window.update_idletasks()
            client = self._window.winfo_id()
            targets = {client, user32.GetParent(client) or client}
            for hwnd in targets:
                if not comctl32.SetWindowSubclass(hwnd, self._proc, 1, 0):
                    continue
                shell32.DragAcceptFiles(hwnd, True)
                # Si la app corre con otros privilegios que el Explorador, Windows bloquea el arrastre salvo que se permita.
                user32.ChangeWindowMessageFilterEx(hwnd, WM_DROPFILES, _MSGFLT_ALLOW, None)
                user32.ChangeWindowMessageFilterEx(hwnd, _WM_COPYGLOBALDATA, _MSGFLT_ALLOW, None)
                self._installed.append(hwnd)
            if self._installed:
                self._deliverer = threading.Thread(target=self._deliver, daemon=True)
                self._deliverer.start()
            return bool(self._installed)
        except Exception:
            logger.exception("No se pudo activar arrastrar y soltar")
            self._installed.clear()
            return False

    def _deliver(self) -> None:
        while (paths := self._pending.get()) is not None:
            try:
                self._window.after(0, self._callback, paths)
            except (RuntimeError, tk.TclError):  # la ventana ya se cerró
                return

    def uninstall(self) -> None:
        self._pending.put(None)
        if not self._installed or self._comctl32 is None:
            return
        try:
            import ctypes

            for hwnd in self._installed:
                ctypes.windll.shell32.DragAcceptFiles(hwnd, False)
                self._comctl32.RemoveWindowSubclass(hwnd, self._proc, 1)
        except Exception:
            logger.exception("No se pudo desactivar arrastrar y soltar")
        self._installed.clear()
