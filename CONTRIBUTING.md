# Contributing

Most valuable: reports for other Proteus 8 versions, new verified SPICE/ISIS element mappings, measurement helpers, and Windows/macOS ideas.

```bash
git clone https://github.com/DontforgetAntaiku/proteus2py && cd proteus2py
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q            # offline tests (this is what CI runs)
ruff check src tests
```

Tests against the real engine are opt-in: `PROTEUS_INTEGRATION=1 PROTEUS2PY_HOME=/path/to/sandbox python -m pytest tests/test_wine.py`
(needs `proteus2py setup <wine prefix with Proteus 8>` first).

## Ground rules
- **Never commit** Proteus binaries, installers, licence data, decompiled code, library dumps or other people's designs/reports.
  Describe findings in prose; build test circuits from code.
- ProSPICE quirks are verified by experiment, not guessed: add a test (`tests/test_wine.py`) with an analytic expected value.
- New behaviour comes with a test, user-visible behaviour with a docs update and a line in `CHANGELOG.md`.
