"""Offline tests of the native (Proteus-authored) builder: geometry, pins, protocol parsing. The real GUI run is in test_wine.py."""
import pytest

from proteus2py import native


def test_px_default_view():
    assert native.px(0, 0) == (971, 541)
    assert native.px(2000, -100) == (1195, 552)


def test_pin_tip_lookup_by_name_and_index():
    c = native.Comp(1, ".?AVCOMPONENT@@", 2000, -100, "U1:A", [native.Pin("A", 1500, 0), native.Pin("B", 1500, -200), native.Pin("Y", 2500, -100)], "74HC00")
    assert c.tip("y") == (2500, -100)
    assert c.tip(1) == (1500, -200)
    with pytest.raises(native.NativeError, match="no pin"):
        c.tip("Z")


def test_catalogues_match_proteus_lists():
    assert native.GENERATORS[:3] == ["DC", "SINE", "PULSE"]
    assert "GROUND" in native.TERMINALS and native.TERMINALS.index("GROUND") == 5
    assert native.GRAPHS[0] == "ANALOGUE" and "FREQUENCY" in native.GRAPHS


def test_hook_roundtrip(tmp_path, monkeypatch):
    """The hook client writes hook.req atomically and parses 'id ok body' / 'id err message' answers."""
    import threading
    import time

    monkeypatch.setenv("PROTEUS2PY_HOME", str(tmp_path))
    h = native.Hook()

    def server(answer: str) -> None:
        req = h.ex / "hook.req"
        for _ in range(100):
            if req.exists():
                i = req.read_text().split()[0]
                req.unlink()
                (h.ex / "hook.resp").write_text(f"{i} {answer}")
                return
            time.sleep(0.02)

    t = threading.Thread(target=server, args=("ok 1234",))
    t.start()
    assert h.req("ping") == "1234"
    t.join()
    t = threading.Thread(target=server, args=("err boom",))
    t.start()
    with pytest.raises(native.NativeError, match="boom"):
        h.req("ping")
    t.join()


def test_native_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "__file__", str(tmp_path / "a" / "b" / "native.py"))
    with pytest.raises(native.NativeError, match="make -C native"):
        native.native_dir()
