# ha-tux

Bridges a Linux host to Home Assistant over MQTT.

It favors least-privilege over convenience: the daemon exposes a remotely triggerable command path, so it runs locked down rather than wide open.

- **MPRIS → media_player**: exposes the host's MPRIS players as a HA media player entity, with album art.
- **ZFS pools**: publishes pool state as HA entities.
- **Package updates**: publishes apt and Homebrew "updates available" as HA update entities (Homebrew gets a working Install button). The full pending-package list lives in a per-manager secret GitHub gist surfaced through a chhoto shortlink.

Installed via `task install` into a self-contained venv at `/opt/ha-tux` and run as two hardened **system** services, one set per host: `ha-tux-session.service` runs as `shyndman` (MPRIS + input presence; needs the session bus, tightened with `NoNewPrivileges=yes`), and `ha-tux-host.service` runs as a locked-down `ha-tux` system user (ZFS + package updates; brew via a shared `brew` group, gh via `GH_TOKEN`). Both are deliberately sandboxed (`ProtectHome=tmpfs`, `ProtectSystem=strict`, a restricted syscall filter, and a tight bind allowlist) — a conscious effort to avoid the gaping holes other HA-on-Linux bridges tend to ship. apt updates are read-only (status only).

The package-updates source needs the `gh` (gist scope) and `chhoto` CLIs on `PATH`; runtime state persists in `$XDG_STATE_HOME/ha-tux/state.toml`.
