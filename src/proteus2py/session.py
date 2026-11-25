"""One Proteus GUI session. Linux: bwrap + Xvfb + Wine + xdotool (everything sandboxed); Windows: the installed Proteus driven with Win32 calls.

Both expose the same API (click, key, type, shot, windows, ...), live only for the duration of one tool call (`once`) and are guarded by a
hard deadline: a watchdog kills the whole process tree when it expires.
"""
from __future__ import annotations

import contextlib
import queue
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from . import workspace as ws

SCREEN = "1920x1080"
PDS = ws.BIN + r"\PDS.EXE"

# One command per stdin line, "OK" per command. Background jobs get </dev/null or they eat the stream.
SERVER = r'''
Xvfb :99 -screen 0 __SCREEN__x24 </dev/null >/dev/null 2>&1 &
export DISPLAY=:99
sleep 2
cd /home/sbx/exchange
wine "__PDS__" "C:\\exchange\\__NAME__.pdsprj" </dev/null >/tmp/w.log 2>&1 &
for i in $(seq 1 __TIMEOUT__); do xdotool search --name "Schematic Capture" >/dev/null 2>&1 && break; sleep 1; done
echo OK
while IFS= read -r line; do
  read -ra a <<< "$line"
  case "${a[0]}" in
    c)  xdotool mousemove $((${a[1]}-3)) $((${a[2]}-2)) sleep 0.05 mousemove ${a[1]} ${a[2]} sleep 0.1 click 1;;
    mv) xdotool mousemove ${a[1]} ${a[2]};;
    k)  xdotool key "${a[@]:1}";;
    t)  xdotool type --delay 40 -- "${line#t }";;
    shot) import -window root "/home/sbx/exchange/${a[1]}.tmp.png" && mv "/home/sbx/exchange/${a[1]}.tmp.png" "/home/sbx/exchange/${a[1]}";;
    wins) xdotool search --onlyvisible --name "" 2>/dev/null | while read w; do xdotool getwindowname $w 2>/dev/null; done | grep -v "^$" | sort -u > /home/sbx/exchange/wins.txt;;
    bg)   eval "${line#bg }" </dev/null >/dev/null 2>&1 &;;
    sh)   eval "${line#sh }" </dev/null >/dev/null 2>&1;;
    quit) wineserver -k 2>/dev/null; kill %1 2>/dev/null; break;;
  esac
  echo OK
done
'''


class SessionError(RuntimeError):
    pass


class SessionTimeout(SessionError):
    """The call exceeded its hard time limit; the whole sandbox was killed."""


class BaseSession(ABC):
    """Platform independent part: deadline/watchdog, load detection, dialogs, graph selection."""
    name: str
    timed_out = False

    def _arm(self, max_seconds: float) -> None:
        self.deadline = time.monotonic() + max_seconds
        self._watchdog = threading.Timer(max_seconds, self._kill)
        self._watchdog.daemon = True
        self._watchdog.start()

    @abstractmethod
    def _kill(self) -> None: ...

    def _check(self) -> None:
        if self.timed_out or time.monotonic() > self.deadline:
            self._kill()
            raise SessionTimeout("session time limit reached; everything was killed (raise max_seconds for long tasks)")

    def sleep(self, seconds: float) -> None:
        """time.sleep that never outlives the session deadline."""
        self._check()
        time.sleep(max(0.0, min(seconds, self.deadline - time.monotonic())))
        self._check()

    # -- primitives every platform implements (screen = 1920x1080 virtual screen, Proteus window at (155, 24))
    @abstractmethod
    def click(self, x: int, y: int) -> None: ...
    @abstractmethod
    def double_click(self, x: int, y: int) -> None: ...
    @abstractmethod
    def key(self, keys: str) -> None: ...
    @abstractmethod
    def type(self, text: str) -> None: ...
    @abstractmethod
    def windows(self) -> list[str]: ...
    @abstractmethod
    def shot(self, file_name: str) -> Path: ...
    @abstractmethod
    def capture_sdf(self, key: str = "space", wait: float = 30.0, tag: str | None = None, graph: int | None = None) -> list[Path]: ...
    @abstractmethod
    def close(self) -> None: ...

    def dialogs(self) -> list[str]:
        """Visible windows other than the Proteus main window and the desktop (error/recovery dialogs)."""
        return [w for w in self.windows() if "Proteus 8" not in w and "Wine" not in w and "Рабочий стол" not in w]

    def wait_stable(self, max_wait: float, interval: float = 1.5, need: int = 3) -> float:
        """Wait until the canvas area is unchanged for `need` consecutive samples (the project finished loading); returns seconds waited."""
        from .gui import region_hash
        start = time.monotonic()
        last, same = None, 0
        while time.monotonic() - start < max_wait:
            self.sleep(interval)
            h = region_hash(self.shot("_stable"))
            same = same + 1 if h == last else 0
            last = h
            if same >= need - 1:
                break
        return time.monotonic() - start

    def select_graph(self, n: int) -> None:
        """Graph menu -> entry n of the graph list (1-based): the graph that Space simulates."""
        self.click(353, 65)
        self.sleep(1.5)
        self.click(430, 269 + 21 * (n - 1))
        self.sleep(1)

    def _start_capture(self, tag: str | None) -> Path:
        d = ws.exchange() / f"cap_{ws.name_ok(tag or self.name)}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        return d

    def _trigger(self, key: str, wait: float, graph: int | None) -> None:
        self.click(1200, 560)
        self.sleep(1)
        if graph:
            self.select_graph(graph)
        self.key(key)
        self.sleep(wait)


class Session(BaseSession):
    """Linux: Xvfb + Wine + Proteus inside bubblewrap, driven with xdotool through a persistent bash on stdin."""

    def __init__(self, name: str, start_timeout: int = 240, load_wait: float = 25, max_seconds: float = 300) -> None:
        """load_wait = MAXIMUM seconds to wait for the project to finish loading (ends early once the screen is stable);
        max_seconds = hard limit for the whole session: a watchdog kills the sandbox (Xvfb, Wine, Proteus) when it is reached."""
        self.name = ws.name_ok(name)
        if not ws.project(name).exists():
            raise FileNotFoundError(f"{ws.project(name)} not found")
        script = (SERVER.replace("__SCREEN__", SCREEN).replace("__PDS__", PDS.replace("\\", "\\\\"))
                  .replace("__NAME__", name).replace("__TIMEOUT__", str(start_timeout)))
        self.p = subprocess.Popen(ws.bwrap(["bash", "-s"]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._arm(max_seconds)
        self.p.stdin.write(script + "\n")
        self.p.stdin.flush()
        if not self._ok(min(start_timeout + 30, max_seconds)):
            self.close()
            raise SessionError("Proteus window did not appear")
        self.wait_stable(load_wait)       # project load continues after the window exists

    def _pump(self) -> None:
        for line in self.p.stdout:
            self.q.put(line.strip())

    def _kill(self) -> None:
        self.timed_out = True
        if self.p.poll() is None:
            self.p.kill()

    def _ok(self, timeout: float) -> bool:
        try:
            return self.q.get(timeout=max(0.1, min(timeout, self.deadline - time.monotonic()))) == "OK"
        except queue.Empty:
            return False

    def cmd(self, line: str, timeout: float = 30) -> None:
        self._check()
        if self.p.poll() is not None:
            raise SessionError("sandbox process died")
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        if not self._ok(timeout):
            self._check()
            raise SessionError(f"no answer to {line!r}")

    def click(self, x: int, y: int) -> None:
        self.cmd(f"c {int(x)} {int(y)}")

    def double_click(self, x: int, y: int) -> None:
        self.cmd(f"sh xdotool mousemove {int(x)} {int(y)} click --repeat 2 --delay 80 1")

    def key(self, keys: str) -> None:
        self.cmd(f"k {keys}")

    def type(self, text: str) -> None:
        if "\n" in text:
            raise ValueError("single line only")
        self.cmd(f"t {text}")

    def sh(self, command: str, timeout: float = 60) -> None:
        """Run a shell command inside the sandbox (waits)."""
        self.cmd("sh " + command, timeout)

    def bg(self, command: str) -> None:
        self.cmd("bg " + command)

    def windows(self) -> list[str]:
        (ws.exchange() / "wins.txt").unlink(missing_ok=True)
        self.cmd("wins")
        time.sleep(0.3)
        f = ws.exchange() / "wins.txt"
        return [x for x in f.read_text(errors="replace").splitlines() if x] if f.exists() else []

    def shot(self, file_name: str) -> Path:
        ws.name_ok(file_name)
        p = ws.exchange() / (file_name if file_name.endswith(".png") else file_name + ".png")
        p.unlink(missing_ok=True)
        self.cmd("mv 1919 1079")                  # park pointer (no tooltips)
        self.cmd(f"shot {p.name}")
        for _ in range(40):                       # the file becomes visible on the host a moment after the command answers
            if p.exists():
                time.sleep(0.15)
                break
            time.sleep(0.15)
        return p

    def capture_sdf(self, key: str = "space", wait: float = 30.0, tag: str | None = None, graph: int | None = None) -> list[Path]:
        """Press `key` (space = graph simulation, F12 = animated) and collect the LISA*.SDF netlists ISIS hands to ProSPICE.
        Files land in exchange/cap_<tag>/ ; returns their paths (one per simulated partition)."""
        d = self._start_capture(tag)
        self.sh("rm -f /tmp/stop_cap; touch /tmp/stamp")
        self.bg("while [ ! -e /tmp/stop_cap ]; do find /home/sbx/.wine/drive_c/users /home/sbx/.wine/drive_c/windows/temp /home/sbx/exchange "
                f"-maxdepth 6 -type f -iname 'LISA*.SDF' -newer /tmp/stamp -not -path '*/cap_*' -exec cp -f {{}} /home/sbx/exchange/{d.name}/ \\; ; sleep 0.1; done")
        self.sleep(0.5)
        self._trigger(key, wait, graph)
        self.sh("touch /tmp/stop_cap")
        return sorted(d.glob("LISA*.SDF"))

    def close(self) -> None:
        """Quit Proteus, Wine and Xvfb; bwrap's own pid namespace (--die-with-parent) guarantees nothing survives."""
        self._watchdog.cancel()
        try:
            if self.p.poll() is None:
                try:
                    self.p.stdin.write("quit\n")
                    self.p.stdin.flush()
                    self.p.wait(timeout=15)
                except Exception:                       # noqa: BLE001
                    pass
        finally:
            if self.p.poll() is None:
                self.p.kill()
            try:
                self.p.wait(timeout=10)
            except Exception:                           # noqa: BLE001
                pass


@contextlib.contextmanager
def once(name: str, load_wait: float = 25, max_seconds: float = 300) -> Iterator[BaseSession]:
    """One-shot session: everything (Xvfb, Wine, Proteus) lives only inside this block and is always torn down afterwards."""
    if ws.IS_WINDOWS:
        from .session_win import WinSession
        s: BaseSession = WinSession(name, load_wait=load_wait, max_seconds=max_seconds)
    else:
        s = Session(name, load_wait=load_wait, max_seconds=max_seconds)
    try:
        yield s
    finally:
        s.close()
