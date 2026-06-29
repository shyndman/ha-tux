from __future__ import annotations

from pathlib import Path

from ha_tux.run_state import ManagerState, StateStore, state_file_path


def test_state_file_path_uses_state_home() -> None:
    assert state_file_path(Path("/x/state")) == Path("/x/state/ha-tux/state.toml")


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.toml"
    store = StateStore.load(path)
    store.update(
        "apt",
        gist_id="abc123",
        short_url="https://go.don.haus/host-apt-updates",
    )
    s = store.get("apt")
    s.notify_anchor = "2026-06-23"
    s.last_install_date = "2026-06-21"
    s.pending_set = ["a", "b"]
    store.save()

    reloaded = StateStore.load(path)
    state = reloaded.get("apt")
    assert state.gist_id == "abc123"
    assert state.short_url == "https://go.don.haus/host-apt-updates"
    assert state.notify_anchor == "2026-06-23"
    assert state.last_install_date == "2026-06-21"
    assert state.pending_set == ["a", "b"]


def test_missing_file_is_empty(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "nope.toml")
    assert store.get("apt") == ManagerState()


def test_update_only_sets_provided_fields(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.toml")
    store.update("brew", gist_id="g1")
    store.update("brew", short_url="https://go.don.haus/host-brew-updates")
    state = store.get("brew")
    assert state.gist_id == "g1"
    assert state.short_url == "https://go.don.haus/host-brew-updates"
    assert state.notify_anchor is None
    assert state.last_install_date is None
    assert state.pending_set == []


def test_save_omits_none_keys(tmp_path: Path) -> None:
    path = tmp_path / "state.toml"
    store = StateStore.load(path)
    store.update("apt", gist_id="abc123")
    store.save()
    out = path.read_text()
    assert 'gist_id = "abc123"' in out
    assert "short_url" not in out
    assert "notify_anchor" not in out
    assert "pending_set" not in out
    assert "[apt]" in out
