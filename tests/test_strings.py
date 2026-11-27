from __future__ import annotations

from ha_tux.foundation.strings import human_bytes, plural_suffix, slugify


def test_slugify_supports_default_and_custom_separator() -> None:
    assert slugify("My Host.local!") == "my-host-local"
    assert slugify("My Host.local!", separator="_") == "my_host_local"


def test_human_bytes() -> None:
    assert human_bytes(512) == "512 B"
    assert human_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert human_bytes(88080384) == "84 MB"


def test_plural_suffix() -> None:
    assert plural_suffix(0) == "s"
    assert plural_suffix(1) == ""
    assert plural_suffix(2) == "s"
