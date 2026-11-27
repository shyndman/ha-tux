"""Immutable per-manager update state and atomic TOML persistence."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast, final

from libsh import get_logger
from xdg_base_dirs import xdg_state_home

from ha_tux.foundation.files import atomic_write_text

LOGGER = get_logger("apt")

STATE_DIRECTORY_NAME: Final = "ha-tux"
STATE_FILE_NAME: Final = "state.toml"


def state_file_path(state_home: Path | None = None) -> Path:
    """Return the XDG state file path for persisted software-update metadata."""
    base = state_home if state_home is not None else xdg_state_home()
    return base / STATE_DIRECTORY_NAME / STATE_FILE_NAME


@dataclass(frozen=True, slots=True)
class ManagerState:
    """Immutable persisted state for a single package manager."""

    gist_id: str | None = None
    short_url: str | None = None
    notify_anchor: str | None = None  # ISO date; frozen latest_version while on
    last_install_date: str | None = None  # ISO date; cooldown anchor
    pending_set: tuple[str, ...] = ()  # sorted last-seen actionable names


def _dump_toml(data: Mapping[str, ManagerState]) -> str:
    lines: list[str] = []
    for manager, state in data.items():
        lines.append(f"[{manager}]")
        for key, value in (
            ("gist_id", state.gist_id),
            ("short_url", state.short_url),
            ("notify_anchor", state.notify_anchor),
            ("last_install_date", state.last_install_date),
        ):
            if value is None:
                continue
            assert '"' not in value and "\n" not in value, (
                f"{manager}.{key} contains a quote or newline: {value!r}"
            )
            lines.append(f'{key} = "{value}"')
        if state.pending_set:
            for name in state.pending_set:
                assert '"' not in name and "\n" not in name, (
                    f"{manager}.pending_set entry has a quote or newline: {name!r}"
                )
            items = ", ".join(f'"{name}"' for name in state.pending_set)
            lines.append(f"pending_set = [{items}]")
        lines.append("")
    return "\n".join(lines)


@final
class StateStore:
    """Loads and atomically persists ``ManagerState`` tables in ``state.toml``."""

    def __init__(self, path: Path, data: dict[str, ManagerState]) -> None:
        self._path: Path = path
        self._data: dict[str, ManagerState] = data

    @classmethod
    def load(cls, path: Path) -> StateStore:
        """Load persisted state from ``path``, or return an empty store if absent."""
        data: dict[str, ManagerState] = {}
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            LOGGER.debug(
                "Software update state file absent; starting with empty state",
                path=os.fspath(path),
            )
            return cls(path, data)

        parsed = cast(dict[str, object], tomllib.loads(raw.decode()))
        for manager, table in parsed.items():
            if not isinstance(table, dict):
                continue
            table_map = cast(dict[str, object], table)
            data[manager] = ManagerState(
                gist_id=_opt_str(table_map.get("gist_id")),
                short_url=_opt_str(table_map.get("short_url")),
                notify_anchor=_opt_str(table_map.get("notify_anchor")),
                last_install_date=_opt_str(table_map.get("last_install_date")),
                pending_set=_opt_str_tuple(table_map.get("pending_set")),
            )
        LOGGER.debug(
            "Loaded software update state",
            path=os.fspath(path),
            managers=sorted(data),
        )
        return cls(path, data)

    def get(self, manager: str) -> ManagerState:
        """Return the current immutable state for ``manager``."""
        return self._data.get(manager, ManagerState())

    def set(self, manager: str, state: ManagerState) -> None:
        """Replace the state entry for ``manager`` in memory."""
        self._data[manager] = state

    def save(self) -> None:
        """Atomically persist the store to disk."""
        atomic_write_text(self._path, _dump_toml(self._data))
        LOGGER.debug("Saved software update state", path=os.fspath(self._path))


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in cast(list[object], value) if isinstance(v, str))
