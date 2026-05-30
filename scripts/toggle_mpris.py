from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME, toggle_playback


def main() -> None:
    previous_status, action = toggle_playback(PLAYERCTLD_SERVICE_NAME)
    print(
        f"{action} sent to {PLAYERCTLD_SERVICE_NAME} because playback was {previous_status}"
    )


if __name__ == "__main__":
    main()
