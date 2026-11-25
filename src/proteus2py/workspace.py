"""Where the sandbox lives: <home>/prefix-pristine, <home>/prefix-work, <home>/exchange (C:\\exchange in Wine)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SB_HOME = "/home/sbx"
IS_WINDOWS = sys.platform == "win32"
_REL = Path("Labcenter Electronics") / "Proteus 8 Professional"
BIN = r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional\BIN"          # as seen from inside Wine
MODELS = r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional\DATA\MODELS"


def proteus_dir() -> Path:
    """Proteus 8 install directory on the HOST (Linux: inside the pristine Wine prefix; Windows: the real install, or $PROTEUS_DIR)."""
    env = os.environ.get("PROTEUS_DIR")
    if env:
        return Path(env)
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), r"C:\Program Files (x86)", r"C:\Program Files"):
            if base and (Path(base) / _REL).exists():
                return Path(base) / _REL
        return Path(r"C:\Program Files (x86)") / _REL
    return home() / "prefix-pristine" / "drive_c" / "Program Files (x86)" / _REL


def guest_path(name: str) -> str:
    """Path of an exchange file as the Proteus tools see it (Wine: C:\\exchange\\name, Windows: the real path)."""
    return str(exchange() / name) if IS_WINDOWS else "C:\\exchange\\" + name


def home() -> Path:
    env = os.environ.get("PROTEUS2PY_HOME") or os.environ.get("PROTEUS_HOME")
    if env:
        return Path(env).expanduser()
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").exists() and (checkout / "sandbox").exists():
        return checkout / "sandbox"                          # running from a source checkout
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "proteus2py"


def exchange() -> Path:
    d = home() / "exchange"
    d.mkdir(parents=True, exist_ok=True)
    return d


def name_ok(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError("name: letters/digits/_.- only")
    return name


def project(name: str) -> Path:
    return exchange() / f"{name_ok(name)}.pdsprj"


def prefix_work() -> Path:
    w, p = home() / "prefix-work", home() / "prefix-pristine"
    if not w.exists():
        if not p.exists():
            raise FileNotFoundError(f"{p} missing: run: proteus2py setup <wine prefix with Proteus 8>")
        subprocess.run(["cp", "-a", "--reflink=auto", str(p), str(w)], check=True)
    return w


def reset() -> None:
    shutil.rmtree(home() / "prefix-work", ignore_errors=True)


def bwrap(args: list[str]) -> list[str]:
    """No network, empty $HOME; only prefix copy and exchange folder are writable."""
    wp, ex = prefix_work(), exchange()
    return ["bwrap", "--die-with-parent", "--unshare-all", "--new-session",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/bin", "/sbin",
            "--ro-bind", "/etc", "/etc", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run",
            "--tmpfs", SB_HOME,
            "--bind", str(wp), f"{SB_HOME}/.wine", "--bind", str(ex), f"{SB_HOME}/exchange",
            "--bind", str(ex), f"{SB_HOME}/.wine/drive_c/exchange",
            "--setenv", "HOME", SB_HOME, "--setenv", "WINEPREFIX", f"{SB_HOME}/.wine", "--setenv", "WINEDEBUG", "-all",
            "--setenv", "WINEDLLOVERRIDES", "winemenubuilder.exe=d;mscoree=d;mshtml=d",
            "--setenv", "LANG", "ru_RU.UTF-8", "--setenv", "LC_ALL", "ru_RU.UTF-8", *args]
