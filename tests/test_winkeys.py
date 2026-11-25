import pytest

from proteus2py import winkeys, workspace


def test_key_parsing():
    assert winkeys.parse("F12") == [0x7B]
    assert winkeys.parse("shift+F12") == [0x10, 0x7B]
    assert winkeys.parse("ctrl+shift+x") == [0x11, 0x10, ord("X")]
    assert winkeys.sequence("Return space 5") == [[0x0D], [0x20], [ord("5")]]
    with pytest.raises(ValueError):
        winkeys.parse("ctrl+nonsense")


def test_guest_path_and_proteus_dir_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "IS_WINDOWS", False)
    assert workspace.guest_path("a.LIB") == "C:\\exchange\\a.LIB"
    monkeypatch.setenv("PROTEUS_DIR", str(tmp_path))
    assert workspace.proteus_dir() == tmp_path
