from __future__ import annotations

from pathlib import Path

from ha_tux.foundation.files import atomic_write_text


def test_atomic_write_text_creates_parents_overwrites_and_cleans_temps(
    tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / "data.toml"

    atomic_write_text(target, "first\n")
    assert target.read_text() == "first\n"

    atomic_write_text(target, "second\n")
    assert target.read_text() == "second\n"
    assert [p.name for p in target.parent.iterdir()] == ["data.toml"]
