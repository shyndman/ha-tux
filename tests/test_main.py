import os
from pathlib import Path
import re
import subprocess
import sys

from pytest import MonkeyPatch

import ha_tux.config as config_module
from ha_tux import DEFAULT_MQTT_URL, build_mqtt_settings, parse_config
from ha_tux.config import BridgeConfig
from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME

SOURCE_PATH = Path(__file__).resolve().parents[1] / "src"


def test_parse_config_uses_defaults(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(config_module, "default_mqtt_client_name", lambda: "test-host")

    config = parse_config(["--once"])

    assert config.once is True
    assert config.mqtt_url == DEFAULT_MQTT_URL
    assert config.mqtt_client_name == "test-host"
    assert config.mpris_service == PLAYERCTLD_SERVICE_NAME


def test_parse_config_uses_environment(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HA_TUX_MQTT_URL", "mqtt://user:pass@mqtt.local:1884")
    monkeypatch.setenv("HA_TUX_MQTT_DISCOVERY_PREFIX", "discovery")
    monkeypatch.setenv("HA_TUX_MQTT_STATE_PREFIX", "state")
    monkeypatch.setenv("HA_TUX_MQTT_CLIENT_NAME", "client")

    config = parse_config(["--once", "--service", "org.example.Player"])

    assert config.mqtt_url == "mqtt://user:pass@mqtt.local:1884"
    assert config.mqtt_discovery_prefix == "discovery"
    assert config.mqtt_state_prefix == "state"
    assert config.mqtt_client_name == "client"
    assert config.mpris_service == "org.example.Player"


def test_build_mqtt_settings_uses_url_config() -> None:
    config = BridgeConfig(
        mqtt_url="mqtt://user:pass@mqtt.local:1884",
        mqtt_discovery_prefix="discovery",
        mqtt_state_prefix="state",
        mqtt_client_name="client",
        mpris_service="org.example.Player",
        position_poll_seconds=1.0,
        zfs_poll_seconds=60.0,
        once=True,
    )

    settings = build_mqtt_settings(config)

    assert settings.url == "mqtt://user:pass@mqtt.local:1884"
    assert settings.username == "user"
    assert settings.password == "pass"
    assert settings.host == "mqtt.local"
    assert settings.port == 1884


def test_module_help_emits_structured_log() -> None:
    python_path = os.fspath(SOURCE_PATH)
    completed = subprocess.run(
        [sys.executable, "-m", "ha_tux", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "JSON_LOGS": "1",
            "PYTHONPATH": python_path,
        },
    )

    assert completed.returncode == 0
    assert "usage: ha-tux" in completed.stdout
    assert completed.stderr
    assert re.search(r'"event"\s*:\s*".+"', completed.stderr)
    assert re.search(r'"logger"\s*:\s*"ha_tux"', completed.stderr)
    assert re.search(r'"level"\s*:\s*"info"', completed.stderr)
