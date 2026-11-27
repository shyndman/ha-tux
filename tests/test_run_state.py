from __future__ import annotations

from pathlib import Path

from ha_tux.software_update.state import ManagerState, StateStore, state_file_path


def test_state_file_path_uses_state_home() -> None:
    assert state_file_path(Path("/x/state")) == Path("/x/state/ha-tux/state.toml")


def test_round_trip_and_atomic_save_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state.toml"
    store = StateStore.load(path)
    store.set(
        "apt",
        ManagerState(
            gist_id="abc123",
            short_url="https://go.don.haus/host-apt-updates",
            notify_anchor="2026-06-23",
            last_install_date="2026-06-21",
            pending_set=("a", "b"),
        ),
    )
    store.save()

    assert [p.name for p in tmp_path.iterdir()] == ["state.toml"]
    reloaded = StateStore.load(path)
    assert reloaded.get("apt") == ManagerState(
        gist_id="abc123",
        short_url="https://go.don.haus/host-apt-updates",
        notify_anchor="2026-06-23",
        last_install_date="2026-06-21",
        pending_set=("a", "b"),
    )


def test_missing_file_is_empty(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "nope.toml")
    assert store.get("apt") == ManagerState()


def test_save_omits_none_keys(tmp_path: Path) -> None:
    path = tmp_path / "state.toml"
    store = StateStore.load(path)
    store.set("apt", ManagerState(gist_id="abc123"))
    store.save()

    out = path.read_text()
    assert 'gist_id = "abc123"' in out
    assert "short_url" not in out
    assert "notify_anchor" not in out
    assert "pending_set" not in out
    assert "[apt]" in out
