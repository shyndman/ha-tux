# ha-tux

Host and desktop integration daemon that publishes Linux machine telemetry and controls to Home Assistant over MQTT discovery.

## Language

**Role**:
A deployment slice (`session`, `host`, `updates`, or `all`) selecting which features a `ha-tux` process runs under its own MQTT client identity.
_Avoid_: mode, profile, service

**Feature**:
An independent host or desktop capability published to Home Assistant, such as `mpris`, `input_active`, `screen_lock`, `zfs`, `power`, `smart`, or `software_update`.
_Avoid_: plugin, integration

**Session Runtime**:
The supervisor that owns the MQTT session lifecycle, reconnect backoff, command-error isolation, and execution of every active feature for a role.
_Avoid_: runner, loop, manager
