import os
from pathlib import Path
import re
import subprocess
import sys

from pytest import MonkeyPatch

import ha_tux.config as config_module
from ha_tux import DEFAULT_MQTT_URL, build_mqtt_settings
from ha_tux.config import HaTuxConfig, MqttConfig, load_config
from ha_tux.media.mpris import PLAYERCTLD_SERVICE_NAME

SOURCE_PATH = Path(__file__).resolve().parents[1] / "src"


def test_load_config_uses_defaults(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(config_module, "default_mqtt_client_name", lambda: "test-host")

    config = load_config(env={})

    assert config.mqtt.url == DEFAULT_MQTT_URL
    assert config.mqtt.client_name == "test-host"
    assert config.mpris.service == PLAYERCTLD_SERVICE_NAME


def test_load_config_uses_environment(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HA_TUX_MQTT_URL", "mqtt://user:pass@mqtt.local:1884")
    monkeypatch.setenv("HA_TUX_MQTT_DISCOVERY_PREFIX", "discovery")
    monkeypatch.setenv("HA_TUX_MQTT_STATE_PREFIX", "state")
    monkeypatch.setenv("HA_TUX_MQTT_CLIENT_NAME", "client")
    monkeypatch.setenv("HA_TUX_MPRIS_SERVICE", "org.example.Player")

    config = load_config()

    assert config.mqtt.url == "mqtt://user:pass@mqtt.local:1884"
    assert config.mqtt.discovery_prefix == "discovery"
    assert config.mqtt.state_prefix == "state"
    assert config.mqtt.client_name == "client"
    assert config.mpris.service == "org.example.Player"


def test_build_mqtt_settings_uses_url_config() -> None:
    config = HaTuxConfig(
        mqtt=MqttConfig(
            url="mqtt://user:pass@mqtt.local:1884",
            discovery_prefix="discovery",
            state_prefix="state",
            client_name="client",
        ),
    )

    settings = build_mqtt_settings(config, "all")

    assert settings.url == "mqtt://user:pass@mqtt.local:1884"
    assert settings.username == "user"
    assert settings.password == "pass"
    assert settings.host == "mqtt.local"
    assert settings.port == 1884


def test_build_mqtt_settings_suffixes_client_name_per_role() -> None:
    config = HaTuxConfig(mqtt=MqttConfig(client_name="ha-tux"))

    assert build_mqtt_settings(config, "session").client_name == "ha-tux-session"
    assert build_mqtt_settings(config, "host").client_name == "ha-tux-host"
    assert build_mqtt_settings(config, "all").client_name == "ha-tux"


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
