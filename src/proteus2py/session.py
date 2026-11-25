"""One Proteus GUI session: bwrap + Xvfb + wine PDS.EXE, driven with xdotool through a persistent bash on stdin."""
from __future__ import annotations

import contextlib
import queue
import subprocess
import threading
import time
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


class Session:
    def __init__(self, name: str, start_timeout: int = 240, load_wait: float = 25) -> None:
        self.name = ws.name_ok(name)
        if not ws.project(name).exists():
            raise FileNotFoundError(f"{ws.project(name)} not found")
        script = (SERVER.replace("__SCREEN__", SCREEN).replace("__PDS__", PDS.replace("\\", "\\\\"))
                  .replace("__NAME__", name).replace("__TIMEOUT__", str(start_timeout)))
        self.p = subprocess.Popen(ws.bwrap(["bash", "-s"]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.p.stdin.write(script + "\n")
        self.p.stdin.flush()
        if not self._ok(start_timeout + 30):
            self.close()
            raise SessionError("Proteus window did not appear")
        time.sleep(load_wait)       # project load continues after the window exists

    def _pump(self) -> None:
        for line in self.p.stdout:
            self.q.put(line.strip())

    def _ok(self, timeout: float) -> bool:
        try:
            return self.q.get(timeout=timeout) == "OK"
        except queue.Empty:
            return False

    def cmd(self, line: str, timeout: float = 30) -> None:
        if self.p.poll() is not None:
            raise SessionError("sandbox process died")
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        if not self._ok(timeout):
            raise SessionError(f"no answer to {line!r}")

    # primitives
    def click(self, x: int, y: int) -> None:
        self.cmd(f"c {int(x)} {int(y)}")

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
        return p

    def select_graph(self, n: int) -> None:
        """Graph menu -> entry n of the graph list (1-based): the graph that Space simulates."""
        self.click(353, 65)
        time.sleep(1.5)
        self.click(430, 269 + 21 * (n - 1))
        time.sleep(1)

    def capture_sdf(self, key: str = "space", wait: float = 30.0, tag: str | None = None, graph: int | None = None) -> list[Path]:
        """Press `key` (space = graph simulation, F12 = animated) and collect the LISA*.SDF netlists ISIS hands to ProSPICE.
        Files land in exchange/cap_<tag>/ ; returns their paths (one per simulated partition)."""
        tag = ws.name_ok(tag or self.name)
        d = ws.exchange() / f"cap_{tag}"
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        self.sh("rm -f /tmp/stop_cap; touch /tmp/stamp")
        self.bg("while [ ! -e /tmp/stop_cap ]; do find /home/sbx/.wine/drive_c/users /home/sbx/.wine/drive_c/windows/temp /home/sbx/exchange "
                f"-maxdepth 6 -type f -iname 'LISA*.SDF' -newer /tmp/stamp -not -path '*/cap_*' -exec cp -f {{}} /home/sbx/exchange/cap_{tag}/ \\; ; sleep 0.1; done")
        time.sleep(0.5)
        self.click(1200, 560)
        time.sleep(1)
        if graph:
            self.select_graph(graph)
        self.key(key)
        time.sleep(wait)
        self.sh("touch /tmp/stop_cap")
        return sorted(d.glob("LISA*.SDF"))

    def close(self) -> None:
        """Quit Proteus, Wine and Xvfb; bwrap's own pid namespace (--die-with-parent) guarantees nothing survives."""
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
def once(name: str, load_wait: float = 25) -> Iterator[Session]:
    """One-shot session: Xvfb + Wine + Proteus run only inside this block and are always torn down afterwards."""
    s = Session(name, load_wait=load_wait)
    try:
        yield s
    finally:
        s.close()
