from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast, final

from xdg_base_dirs import xdg_state_home

STATE_DIRECTORY_NAME: Final = "ha-tux"
STATE_FILE_NAME: Final = "state.toml"


def state_file_path(state_home: Path | None = None) -> Path:
    base = state_home if state_home is not None else xdg_state_home()
    return base / STATE_DIRECTORY_NAME / STATE_FILE_NAME


@dataclass(slots=True)
class ManagerState:
    gist_id: str | None = None
    short_url: str | None = None
    notify_anchor: str | None = None  # ISO date; frozen latest_version while on
    last_install_date: str | None = None  # ISO date; cooldown anchor
    pending_set: list[str] = field(default_factory=list)  # last-seen actionable names


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
    def __init__(self, path: Path, data: dict[str, ManagerState]) -> None:
        self._path: Path = path
        self._data: dict[str, ManagerState] = data

    @classmethod
    def load(cls, path: Path) -> StateStore:
        data: dict[str, ManagerState] = {}
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return cls(path, data)
        parsed = cast(dict[str, object], tomllib.loads(raw.decode()))
        for manager, table in parsed.items():
            if not isinstance(table, dict):
                continue
            table = cast(dict[str, object], table)
            data[manager] = ManagerState(
                gist_id=_opt_str(table.get("gist_id")),
                short_url=_opt_str(table.get("short_url")),
                notify_anchor=_opt_str(table.get("notify_anchor")),
                last_install_date=_opt_str(table.get("last_install_date")),
                pending_set=_opt_str_list(table.get("pending_set")),
            )
        return cls(path, data)

    def get(self, manager: str) -> ManagerState:
        state = self._data.get(manager)
        if state is None:
            state = ManagerState()
            self._data[manager] = state
        return state

    def update(
        self,
        manager: str,
        *,
        gist_id: str | None = None,
        short_url: str | None = None,
    ) -> None:
        state = self.get(manager)
        if gist_id is not None:
            state.gist_id = gist_id
        if short_url is not None:
            state.short_url = short_url

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _ = self._path.write_text(_dump_toml(self._data))


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in cast(list[object], value) if isinstance(v, str)]
