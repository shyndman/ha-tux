from typing import Protocol

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable.media_player import MediaPlayerInfo

from ha_tux.host_device import build_host_device_info

HA_TUX_MEDIA_OBJECT_ID = "desktop_media"
HA_TUX_MEDIA_NAME = "Desktop Media"
HA_TUX_MEDIA_DEVICE_CLASS = "speaker"
PUBLISHER_NOT_INITIALIZED = "Media player publisher was not initialized"


class MediaPlayerPublisher(Protocol):
    """The async subset of ``ha_mqtt_discoverable.media_player.MediaPlayer`` the
    bridge drives. State is published over the shared :class:`MqttSession`."""

    async def set_available(self, available: bool) -> None: ...

    async def set_state(self, state: str) -> None: ...

    async def set_title(self, title: str) -> None: ...

    async def set_artist(self, artist: str) -> None: ...

    async def set_album(self, album: str) -> None: ...

    async def set_duration(self, duration: int) -> None: ...

    async def set_position(self, position: int) -> None: ...

    async def set_volume(self, volume: float) -> None: ...

    async def set_muted(self, muted: bool) -> None: ...

    async def set_albumart_url(self, url: str) -> None: ...

    async def set_media_image_remotely_accessible(self, accessible: bool) -> None: ...


class PlaceholderPublisher:
    """Stand-in held by the bridge until the real ``MediaPlayer`` is wired in.

    Every method raises so a missing initialization fails loudly instead of
    silently dropping published state."""

    async def set_available(self, available: bool) -> None:
        del available
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_state(self, state: str) -> None:
        del state
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_title(self, title: str) -> None:
        del title
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_artist(self, artist: str) -> None:
        del artist
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_album(self, album: str) -> None:
        del album
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_duration(self, duration: int) -> None:
        del duration
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_position(self, position: int) -> None:
        del position
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_volume(self, volume: float) -> None:
        del volume
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_muted(self, muted: bool) -> None:
        del muted
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_albumart_url(self, url: str) -> None:
        del url
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    async def set_media_image_remotely_accessible(self, accessible: bool) -> None:
        del accessible
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)


def build_media_player_entity(
    device: DeviceInfo | None = None, *, host_prefix: str
) -> MediaPlayerInfo:
    resolved_device = device if device is not None else build_host_device_info()
    return MediaPlayerInfo(
        name=HA_TUX_MEDIA_NAME,
        object_id=f"{host_prefix}_{HA_TUX_MEDIA_OBJECT_ID}",
        unique_id=f"ha_tux_{host_prefix}_{HA_TUX_MEDIA_OBJECT_ID}",
        device=resolved_device,
        device_class=HA_TUX_MEDIA_DEVICE_CLASS,
    )
