# ha-tux

Bridges a Linux host to Home Assistant over MQTT.

- **MPRIS → media_player**: exposes the host's MPRIS players as a HA media player entity, with album art.
- **ZFS pools**: publishes pool state as HA entities.

Runs as a systemd unit (`systemd/ha-tux.service`), one instance per host.
