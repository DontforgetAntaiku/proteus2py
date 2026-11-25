import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PROTEUS_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="needs the Wine sandbox: set PROTEUS_INTEGRATION=1 (and PROTEUS2PY_HOME)")
    for it in items:
        if "wine" in it.keywords:
            it.add_marker(skip)
