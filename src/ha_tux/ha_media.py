from typing import Protocol, override

from ha_mqtt_discoverable import DeviceInfo, Settings
from ha_mqtt_discoverable.media_player import MediaPlayer, MediaPlayerInfo

HA_TUX_DEVICE_IDENTIFIER = "ha-tux"
HA_TUX_MEDIA_OBJECT_ID = "desktop_media"
HA_TUX_MEDIA_UNIQUE_ID = "ha_tux_desktop_media"
HA_TUX_MEDIA_NAME = "Desktop Media"
PUBLISHER_NOT_INITIALIZED = "Media player publisher was not initialized"


class MediaPlayerPublisher(Protocol):
    def set_availability(self, availability: bool) -> None: ...

    def set_state(self, state: str) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_artist(self, artist: str) -> None: ...

    def set_album(self, album: str) -> None: ...

    def set_duration(self, duration: int) -> None: ...

    def set_position(self, position: int) -> None: ...

    def set_volume(self, volume: float) -> None: ...

    def set_muted(self, muted: bool) -> None: ...

    def set_albumart_url(self, url: str) -> None: ...

    def set_media_image_remotely_accessible(self, accessible: bool) -> None: ...

    def close(self) -> None: ...


class NonConnectingMediaPlayer(MediaPlayer):
    @override
    def _connect_client(self) -> None:
        return


class PlaceholderPublisher:
    def set_availability(self, availability: bool) -> None:
        del availability
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_state(self, state: str) -> None:
        del state
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_title(self, title: str) -> None:
        del title
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_artist(self, artist: str) -> None:
        del artist
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_album(self, album: str) -> None:
        del album
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_duration(self, duration: int) -> None:
        del duration
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_position(self, position: int) -> None:
        del position
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_volume(self, volume: float) -> None:
        del volume
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_muted(self, muted: bool) -> None:
        del muted
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_albumart_url(self, url: str) -> None:
        del url
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def set_media_image_remotely_accessible(self, accessible: bool) -> None:
        del accessible
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)

    def close(self) -> None:
        raise RuntimeError(PUBLISHER_NOT_INITIALIZED)


def build_media_player_settings(
    *,
    mqtt: Settings.MQTT,
    manual_availability: bool = True,
) -> Settings[MediaPlayerInfo]:
    device = DeviceInfo(name="ha-tux", identifiers=HA_TUX_DEVICE_IDENTIFIER)
    entity = MediaPlayerInfo(
        name=HA_TUX_MEDIA_NAME,
        object_id=HA_TUX_MEDIA_OBJECT_ID,
        unique_id=HA_TUX_MEDIA_UNIQUE_ID,
        device=device,
        device_class="speaker",
    )
    return Settings[MediaPlayerInfo](
        mqtt=mqtt,
        entity=entity,
        manual_availability=manual_availability,
    )
