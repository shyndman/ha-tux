# ha-tux

Bridges a Linux host to Home Assistant over MQTT.

- **MPRIS → media_player**: exposes the host's MPRIS players as a HA media player entity, with album art.
- **ZFS pools**: publishes pool state as HA entities.
- **Package updates**: publishes apt and Homebrew "updates available" as HA update entities (Homebrew gets a working Install button). The full pending-package list lives in a per-manager secret GitHub gist surfaced through a chhoto shortlink.

Runs as a systemd unit (`systemd/ha-tux.service`), one instance per host.

The package-updates source needs the `gh` (gist scope) and `chhoto` CLIs on `PATH`; runtime state persists in `$XDG_STATE_HOME/ha-tux/state.toml`.
