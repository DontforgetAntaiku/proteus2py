# Development

* `pip install -e '.[dev]'`; `pytest` (offline: golden SDF netlists, decoders, spec validation); `ruff check src tests`.
* Real-engine tests: `proteus2py setup <prefix>` then `PROTEUS_INTEGRATION=1 pytest tests/test_wine.py`. **Run them before every release.**
* Add an element: write a handler with `@element("KIND")` in `elements.py` (JSON specs) or `@spice_element("x")` in `spice.py` (decks); add a golden SDF in
  `tests/test_golden_sdf.py` and an analytic check in `tests/test_wine.py`.
* Add an analysis: `@analysis(MyModel)` in `analyses.py` plus a model in `models.py`.
* Format notes: [ProSPICE](NOTES_prospice.md), [.pdsprj](PDSPRJ.md).
