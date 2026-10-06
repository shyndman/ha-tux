<img src="assets/logo.png" alt="ha-tux" width="128" align="right">

# ha-tux

Bridges a Linux host to Home Assistant over MQTT.

It favors least-privilege over convenience: the daemon exposes a remotely triggerable command path, so it runs locked down rather than wide open.

- **MPRIS → media_player**: exposes the host's MPRIS players as a HA media player entity, with album art.
- **ZFS pools**: publishes pool state as HA entities.
- **Package updates**: publishes apt and Homebrew "updates available" as HA update entities (Homebrew gets a working Install button). The full pending-package list lives in a per-manager secret GitHub gist surfaced through a chhoto shortlink.

`task install` installs a self-contained virtual environment at `/opt/ha-tux` and starts three system services:

- `ha-tux-session.service` runs as `shyndman` for MPRIS and input presence.
- `ha-tux-host.service` runs as `ha-tux` for ZFS, power, and SMART state.
- `ha-tux-updates.service` runs as `shyndman` for apt and Homebrew update checks.

All three services use `ProtectHome=tmpfs`, `ProtectSystem=strict`, `NoNewPrivileges=yes`, a restricted syscall filter, and explicit mounts.
The update service can write to the Homebrew prefix and `/var/lib/ha-tux-updates`, but cannot access your personal files.
Homebrew uses your account's ownership and permissions. The installer does not add shared group-write access or Git ownership exceptions.
The update service delegates upgrades to `ha-tux-apt-upgrade.service` and `ha-tux-brew-upgrade.service`. Polkit authorizes your account to start these services.

The session service mounts `/home/shyndman/.mozilla/firefox/firefox-mpris` read-only to read Firefox album art. If the directory does not exist at service start, the mount is skipped. Restart the session service after Firefox creates it.

The package-update service needs the `gh` and `chhoto` commands on `PATH`. Supply `GH_TOKEN` with gist access in `/etc/ha-tux/host.env`.
The update service loads this file and uses `/var/lib/ha-tux-updates` as its home directory.
Its config is in `.config/ha-tux/config.toml`, and its state is in `.local/state/ha-tux/state.toml` under that directory.
The installer preserves existing update state on later installs.
