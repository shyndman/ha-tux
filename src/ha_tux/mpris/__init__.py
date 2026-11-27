"""MPRIS desktop media-player feature package."""

from ha_tux.mpris.bridge import (
    MprisBridge,
    build_media_player_entity,
    run_mpris,
)
from ha_tux.mpris.player import (
    DEFAULT_POSITION_POLL_SECONDS,
    PLAYERCTLD_SERVICE_NAME,
)

__all__ = [
    "DEFAULT_POSITION_POLL_SECONDS",
    "PLAYERCTLD_SERVICE_NAME",
    "MprisBridge",
    "build_media_player_entity",
    "run_mpris",
]
