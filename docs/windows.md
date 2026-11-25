# Windows

* **Headless simulation** (`simulate`, `simulate_spice`, `measure` ...) runs the installed `PROSPICE.EXE` directly: no Wine, no sandbox. Point `PROTEUS_DIR`
  at the install folder if it is not in `Program Files (x86)`.
* **GUI tools** (`gui_run`, `simulate_project`) use a Win32 driver (`session_win.py`): it starts `PDS.EXE`, moves the window to a fixed place, and drives it
  with `SendInput`-style calls. **It has not been tested on real Windows yet**; reports and fixes are welcome (see CONTRIBUTING).
* The same hard `max_seconds` limit applies; on expiry `taskkill /T /F` ends Proteus.
