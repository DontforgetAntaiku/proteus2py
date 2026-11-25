# Getting started

## 1. Install
```bash
pip install proteus2py            # or: uvx proteus2py-mcp   (no install)
pip install 'proteus2py[plot,fast]'   # optional: matplotlib plots, numpy harmonics
```
Linux needs `bubblewrap`, `wine` and (for the GUI tools) `xorg-server-xvfb`, `xdotool`, `imagemagick`. Check with `proteus2py doctor`.

## 2. Put Proteus into a Wine prefix
proteus2py never touches your own Wine prefix: it works on a private, sanitised copy.

1. Create a prefix and install Proteus 8 Professional into it with your own installer and licence:
   ```bash
   export WINEPREFIX=$HOME/.wine-proteus
   wineboot -i
   wine /path/to/Proteus8Pro_setup.exe        # install to the default folder, run it once, activate the licence
   ```
   Proteus must end up in `C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional`.
2. Let proteus2py copy and harden it (about 2 GB; symlinks into your home and the `z:` drive are removed):
   ```bash
   proteus2py setup ~/.wine-proteus
   proteus2py doctor
   ```
3. Try it: `proteus2py spice examples/rc_lowpass.cir` (prints a -3 dB point of about 1000 Hz).

The data lives in `~/.local/share/proteus2py` (override with `PROTEUS2PY_HOME`). Delete `prefix-work` to reset the sandbox.

## 3. Use it from an AI assistant
```json
{ "mcpServers": { "proteus": { "command": "uvx", "args": ["proteus2py-mcp"] } } }
```
Claude Code: `claude mcp add proteus -- uvx proteus2py-mcp`.

## 4. Use it from Python
```python
from proteus2py import sim, spice
r = sim.simulate(spice.parse(open("examples/ce_amplifier.cir").read()), "ce")
print(r.stats["v(c)"].rms, sim.measure_run("ce", "harmonics", "v(c)", f0=1000)["thd_pct"])
```

## Troubleshooting
* *"prefix-pristine missing"* - run `proteus2py setup`.
* *GUI call hangs/very slow* - raise `load_wait` / `max_seconds`; a watchdog kills the sandbox at `max_seconds`.
* *Two Wine jobs at once break each other* - use a second `PROTEUS2PY_HOME` (copy `prefix-pristine`).
