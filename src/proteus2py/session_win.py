"""Windows driver for the Proteus GUI (EXPERIMENTAL: written against the Win32 API, not yet tested on a real Windows machine).

Same API as the Linux `Session`. The Proteus window is moved to (155, 24) with size 1487x956 so that the coordinates used by the tools
(taken on the 1920x1080 Xvfb screen) stay valid; screenshots are padded onto a 1920x1080 canvas for the same reason.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

from . import winkeys
from . import workspace as ws
from .session import BaseSession, SessionError

WIN_X, WIN_Y, WIN_W, WIN_H = 155, 24, 1487, 956
SW_RESTORE, SWP_NOZORDER = 9, 0x0004
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004


class WinSession(BaseSession):
    def __init__(self, name: str, start_timeout: int = 240, load_wait: float = 25, max_seconds: float = 300) -> None:
        if not ws.IS_WINDOWS:
            raise SessionError("WinSession needs Windows")
        self.name = ws.name_ok(name)
        proj = ws.project(name)
        if not proj.exists():
            raise FileNotFoundError(f"{proj} not found")
        self.u32 = ctypes.windll.user32
        self._arm(max_seconds)
        exe = ws.proteus_dir() / "BIN" / "PDS.EXE"
        self.p = subprocess.Popen([str(exe), str(proj)], cwd=str(ws.exchange()))
        self.hwnd = self._find_main(start_timeout)
        self._place()
        self.wait_stable(load_wait)

    # -- window helpers
    def _top_windows(self) -> list[tuple[int, str, int]]:
        out: list[tuple[int, str, int]] = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(h: int, _l: int) -> bool:
            if self.u32.IsWindowVisible(h):
                n = self.u32.GetWindowTextLengthW(h)
                buf = ctypes.create_unicode_buffer(n + 1)
                self.u32.GetWindowTextW(h, buf, n + 1)
                pid = wintypes.DWORD()
                self.u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                out.append((h, buf.value, pid.value))
            return True
        self.u32.EnumWindows(proto(cb), 0)
        return out

    def _find_main(self, timeout: float) -> int:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._check()
            for h, title, pid in self._top_windows():
                if pid == self.p.pid and "Schematic Capture" in title:
                    return h
            if self.p.poll() is not None:
                raise SessionError("Proteus exited during start-up")
            time.sleep(0.5)
        self.close()
        raise SessionError("Proteus window did not appear")

    def _place(self) -> None:
        self.u32.ShowWindow(self.hwnd, SW_RESTORE)
        self.u32.SetWindowPos(self.hwnd, 0, WIN_X, WIN_Y, WIN_W, WIN_H, SWP_NOZORDER)
        self.u32.SetForegroundWindow(self.hwnd)
        time.sleep(0.5)

    def _kill(self) -> None:
        self.timed_out = True
        self._taskkill()

    def _taskkill(self) -> None:
        if self.p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.p.pid)], capture_output=True)

    # -- input
    def _move(self, x: int, y: int) -> None:
        self._check()
        self.u32.SetForegroundWindow(self.hwnd)
        self.u32.SetCursorPos(int(x), int(y))
        time.sleep(0.1)

    def click(self, x: int, y: int) -> None:
        self._move(x, y)
        self.u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        self.u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.2)

    def double_click(self, x: int, y: int) -> None:
        self.click(x, y)
        self.click(x, y)

    def _press(self, combo: list[int]) -> None:
        for k in combo:
            self.u32.keybd_event(k, 0, 0, 0)
        for k in reversed(combo):
            self.u32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)

    def key(self, keys: str) -> None:
        self._check()
        self.u32.SetForegroundWindow(self.hwnd)
        for combo in winkeys.sequence(keys):
            self._press(combo)

    def type(self, text: str) -> None:
        if "\n" in text:
            raise ValueError("single line only")
        self._check()
        self.u32.SetForegroundWindow(self.hwnd)
        for ch in text:
            res = self.u32.VkKeyScanW(ord(ch))
            code, shift = res & 0xFF, bool(res & 0x100)
            self._press(([0x10] if shift else []) + [code])

    # -- observation
    def windows(self) -> list[str]:
        return sorted({t for _h, t, pid in self._top_windows() if pid == self.p.pid and t})

    def shot(self, file_name: str) -> Path:
        from PIL import Image, ImageGrab
        ws.name_ok(file_name)
        p = ws.exchange() / (file_name if file_name.endswith(".png") else file_name + ".png")
        self._move(1919, 1079)                    # park the pointer (no tooltips)
        self.u32.SetForegroundWindow(self.hwnd)
        time.sleep(0.2)
        img = ImageGrab.grab(bbox=(WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H), all_screens=True)
        canvas = Image.new("RGB", (1920, 1080), (0, 0, 0))
        canvas.paste(img.convert("RGB"), (WIN_X, WIN_Y))
        canvas.save(p)
        return p

    def capture_sdf(self, key: str = "space", wait: float = 30.0, tag: str | None = None, graph: int | None = None) -> list[Path]:
        """Collect the LISA*.SDF files ISIS writes (temp folder / project folder) while the simulation starts."""
        d = self._start_capture(tag)
        stop = threading.Event()
        stamp = time.time()
        seen: set[Path] = set()
        roots = {Path(tempfile.gettempdir()), ws.exchange(), Path(os.environ.get("LOCALAPPDATA", "")) / "Temp"}

        def poll() -> None:
            while not stop.is_set():
                for r in roots:
                    for f in list(r.glob("LISA*.SDF")) + list(r.glob("*/LISA*.SDF")):
                        if f not in seen and f.stat().st_mtime >= stamp and d not in f.parents:
                            try:
                                shutil.copy(f, d / f.name)
                                seen.add(f)
                            except OSError:
                                pass
                time.sleep(0.1)
        t = threading.Thread(target=poll, daemon=True)
        t.start()
        try:
            self._trigger(key, wait, graph)
        finally:
            stop.set()
            t.join(timeout=2)
        return sorted(d.glob("LISA*.SDF"))

    def close(self) -> None:
        self._watchdog.cancel()
        self._taskkill()
        try:
            self.p.wait(timeout=10)
        except Exception:                               # noqa: BLE001
            pass
