from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

import ha_tux.config as config_module
from ha_tux.config import (
    DEFAULT_CONFIG_FILE_TEXT,
    DEFAULT_MQTT_URL,
    AppConfig,
    BridgeConfig,
    ConfigError,
    config_file_path,
    format_config_for_log,
    load_app_config,
    parse_config,
    read_config_file,
    write_default_config_file,
)
from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME


@pytest.fixture(autouse=True)
def default_client_name(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_module,
        "default_mqtt_client_name",
        lambda: "test-host",
    )


def test_missing_config_file_returns_defaults_and_writes_template(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ha-tux" / "config.toml"

    app_config = load_app_config(path=path, env={})

    assert app_config.mqtt.url == DEFAULT_MQTT_URL
    assert app_config.mqtt.client_name == "test-host"
    assert app_config.mpris.service == PLAYERCTLD_SERVICE_NAME
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_FILE_TEXT
    assert all(
        line.startswith("#")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


def test_default_config_writer_never_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "ha-tux" / "config.toml"
    path.parent.mkdir()
    _ = path.write_text('[mqtt]\nurl = "mqtt://mqtt.local:1883"\n', encoding="utf-8")

    did_write = write_default_config_file(path)

    assert did_write is False
    assert path.read_text(encoding="utf-8") == (
        '[mqtt]\nurl = "mqtt://mqtt.local:1883"\n'
    )


def test_config_file_path_uses_provided_config_home() -> None:
    assert config_file_path(config_home=Path("/tmp/example")) == Path(
        "/tmp/example/ha-tux/config.toml"
    )


def test_config_file_path_honors_absolute_xdg_config_home(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_file_path() == tmp_path / "ha-tux" / "config.toml"


def test_config_file_path_uses_home_default_for_relative_xdg_config_home(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-config")

    assert config_file_path() == home / ".config" / "ha-tux" / "config.toml"


def test_toml_file_values_populate_app_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text(
        """
[mqtt]
url = "mqtts://user:secret@mqtt.local:8883"
discovery_prefix = "discovery"
state_prefix = "state"
client_name = "client"

[mpris]
service = "org.example.Player"

[bridge]
position_poll_seconds = 2.5
zfs_poll_seconds = 30.0
""".strip(),
        encoding="utf-8",
    )

    app_config = load_app_config(path=path, env={})

    assert app_config.mqtt.url == "mqtts://user:secret@mqtt.local:8883"
    assert app_config.mqtt.discovery_prefix == "discovery"
    assert app_config.mqtt.state_prefix == "state"
    assert app_config.mqtt.client_name == "client"
    assert app_config.mpris.service == "org.example.Player"
    assert app_config.bridge.position_poll_seconds == 2.5
    assert app_config.bridge.zfs_poll_seconds == 30.0


def test_environment_overrides_toml_file_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text(
        """
[mqtt]
url = "mqtt://file-user:file-secret@file-host:1883"
discovery_prefix = "file-discovery"
state_prefix = "file-state"
client_name = "file-client"

[mpris]
service = "org.example.File"

[bridge]
position_poll_seconds = 1.5
""".strip(),
        encoding="utf-8",
    )

    app_config = load_app_config(
        path=path,
        env={
            "HA_TUX_MQTT_URL": "wss://env-user:env-secret@env-host:443/mqtt",
            "HA_TUX_MQTT_DISCOVERY_PREFIX": "env-discovery",
            "HA_TUX_MQTT_STATE_PREFIX": "env-state",
            "HA_TUX_MQTT_CLIENT_NAME": "env-client",
            "HA_TUX_MPRIS_SERVICE": "org.example.Env",
            "HA_TUX_POSITION_POLL_SECONDS": "3.5",
            "HA_TUX_ZFS_POLL_SECONDS": "90.0",
        },
    )

    assert app_config.mqtt.url == "wss://env-user:env-secret@env-host:443/mqtt"
    assert app_config.mqtt.discovery_prefix == "env-discovery"
    assert app_config.mqtt.state_prefix == "env-state"
    assert app_config.mqtt.client_name == "env-client"
    assert app_config.mpris.service == "org.example.Env"
    assert app_config.bridge.position_poll_seconds == 3.5
    assert app_config.bridge.zfs_poll_seconds == 90.0


def test_cli_flags_override_environment_and_toml(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "ha-tux" / "config.toml"
    path.parent.mkdir()
    _ = path.write_text(
        """
[mpris]
service = "org.example.File"

[bridge]
position_poll_seconds = 1.5
""".strip(),
        encoding="utf-8",
    )

    bridge_config = parse_config(
        [
            "--once",
            "--service",
            "org.example.Cli",
            "--position-poll-seconds",
            "4.5",
            "--zfs-poll-seconds",
            "120.0",
        ],
        env={
            "HA_TUX_MPRIS_SERVICE": "org.example.Env",
            "HA_TUX_POSITION_POLL_SECONDS": "3.5",
            "HA_TUX_ZFS_POLL_SECONDS": "90.0",
        },
    )

    assert bridge_config.once is True
    assert bridge_config.mpris_service == "org.example.Cli"
    assert bridge_config.position_poll_seconds == 4.5
    assert bridge_config.zfs_poll_seconds == 120.0
    assert bridge_config.mqtt_client_name == "test-host"


def test_empty_environment_variables_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text('[mqtt]\nurl = "mqtt://file-host:1883"\n', encoding="utf-8")

    app_config = load_app_config(
        path=path,
        env={
            "HA_TUX_MQTT_URL": "",
        },
    )

    assert app_config.mqtt.url == "mqtt://file-host:1883"


def test_invalid_toml_raises_config_error_with_path(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("not valid toml =", encoding="utf-8")

    with pytest.raises(ConfigError, match=f"Invalid TOML in {path}"):
        _ = read_config_file(path)


def test_extra_toml_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text('[mqtt]\nunknown = "value"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match=f"Invalid config file {path}"):
        _ = load_app_config(path=path, env={})


def test_invalid_url_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text('[mqtt]\nurl = "http://mqtt.local:1883"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match=f"Invalid config file {path}"):
        _ = load_app_config(path=path, env={})


def test_non_positive_position_poll_seconds_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("[bridge]\nposition_poll_seconds = 0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=f"Invalid config file {path}"):
        _ = load_app_config(path=path, env={})


def test_non_positive_zfs_poll_seconds_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("[bridge]\nzfs_poll_seconds = 0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=f"Invalid config file {path}"):
        _ = load_app_config(path=path, env={})


def test_pretty_config_logging_renders_all_non_secret_fields() -> None:
    bridge_config = BridgeConfig(
        mqtt_url="ws://mqtt.local:8080/mqtt",
        mqtt_discovery_prefix="discovery",
        mqtt_state_prefix="state",
        mqtt_client_name="client",
        mpris_service="org.example.Player",
        position_poll_seconds=2.5,
        zfs_poll_seconds=60.0,
        once=True,
    )

    rendered = format_config_for_log(bridge_config)

    assert (
        rendered
        == """[mqtt]
url = "ws://mqtt.local:8080/mqtt"
discovery_prefix = "discovery"
state_prefix = "state"
client_name = "client"

[mpris]
service = "org.example.Player"

[bridge]
position_poll_seconds = 2.5
zfs_poll_seconds = 60.0"""
    )


def test_pretty_config_logging_redacts_password() -> None:
    bridge_config = BridgeConfig(
        mqtt_url="mqtt://user:secret-password@mqtt.local:1884",
        mqtt_discovery_prefix="discovery",
        mqtt_state_prefix="state",
        mqtt_client_name="client",
        mpris_service="org.example.Player",
        position_poll_seconds=2.5,
        zfs_poll_seconds=60.0,
        once=True,
    )

    rendered = format_config_for_log(bridge_config)

    assert 'url = "mqtt://<redacted>:<redacted>@mqtt.local:1884"' in rendered
    assert "user" not in rendered
    assert "secret-password" not in rendered


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.records.append((event, kwargs))


def test_parse_config_logs_path_status_and_redacted_pretty_config(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    logger = CapturingLogger()

    def get_capturing_logger(_name: str) -> CapturingLogger:
        return logger

    monkeypatch.setattr(config_module, "get_logger", get_capturing_logger)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "ha-tux" / "config.toml"
    path.parent.mkdir()
    _ = path.write_text(
        """
[mqtt]
url = "mqtt://log-user:secret-password@mqtt.local:1883"
""".strip(),
        encoding="utf-8",
    )

    _ = parse_config(["--once"], env={})

    assert ("config_file_loaded", {"path": str(path)}) in logger.records
    startup_records = [
        kwargs for event, kwargs in logger.records if event == "startup_configuration"
    ]
    assert len(startup_records) == 1
    configuration = startup_records[0]["configuration"]
    assert isinstance(configuration, str)
    assert "[mqtt]" in configuration
    assert 'url = "mqtt://<redacted>:<redacted>@mqtt.local:1883"' in configuration
    assert 'client_name = "test-host"' in configuration
    assert "log-user" not in configuration
    assert "secret-password" not in configuration


def test_app_config_is_constructible_for_type_checking() -> None:
    app_config = AppConfig()

    assert app_config.mqtt.url == DEFAULT_MQTT_URL
    assert app_config.mqtt.client_name == "test-host"


def test_validation_error_from_environment_names_env_vars(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="HA_TUX_MQTT_URL"):
        _ = load_app_config(path=path, env={"HA_TUX_MQTT_URL": "not-a-url"})


def test_config_file_template_text_has_no_active_lines() -> None:
    assert all(
        line.startswith("#") for line in DEFAULT_CONFIG_FILE_TEXT.splitlines() if line
    )


def test_config_file_data_is_plain_mapping(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text('[mqtt]\nurl = "mqtt://mqtt.local:1883"\n', encoding="utf-8")

    data: dict[str, object] = read_config_file(path)

    assert data == {"mqtt": {"url": "mqtt://mqtt.local:1883"}}
