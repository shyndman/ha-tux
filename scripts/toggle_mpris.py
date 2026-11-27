"""Standalone helper script to toggle MPRIS playback via D-Bus."""

from __future__ import annotations

import asyncio
from typing import Literal

from ha_tux.mpris.player import PLAYERCTLD_SERVICE_NAME, MprisPlayer, SdbusMprisPlayer

ToggleAction = Literal["Pause", "Play"]


def select_toggle_action(playback_status: str) -> ToggleAction:
    """Return ``Pause`` if playing, or ``Play`` if paused or stopped."""
    if playback_status == "Playing":
        return "Pause"

    if playback_status in {"Paused", "Stopped"}:
        return "Play"

    raise ValueError(f"Unsupported playback status: {playback_status}")


async def toggle_player_async(player: MprisPlayer) -> tuple[str, ToggleAction]:
    """Toggle ``player`` between playing and paused/stopped if supported."""
    playback_status = await player.get_playback_status()
    action = select_toggle_action(playback_status)

    if action == "Pause":
        if not await player.get_can_pause():
            raise RuntimeError("MPRIS player reported that pause is unsupported")

        await player.pause()
        return playback_status, action

    if not await player.get_can_play():
        raise RuntimeError("MPRIS player reported that play is unsupported")

    await player.play()
    return playback_status, action


async def toggle_playback_async(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> tuple[str, ToggleAction]:
    """Connect to ``service_name`` and toggle playback."""
    return await toggle_player_async(SdbusMprisPlayer(service_name))


def toggle_playback(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> tuple[str, ToggleAction]:
    """Synchronously toggle playback on ``service_name``."""
    return asyncio.run(toggle_playback_async(service_name))


def main() -> None:
    """Toggle default playerctld playback and print the transition."""
    previous_status, action = toggle_playback(PLAYERCTLD_SERVICE_NAME)
    print(
        f"{action} sent to {PLAYERCTLD_SERVICE_NAME} because playback was {previous_status}"
    )


if __name__ == "__main__":
    main()
