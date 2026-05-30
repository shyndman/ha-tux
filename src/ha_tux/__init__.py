from libsh import get_logger, setup_logging_from_env

LOGGER_NAME = "ha_tux"
STARTUP_EVENT = "application_started"


def main() -> None:
    setup_logging_from_env()
    get_logger(LOGGER_NAME).info(STARTUP_EVENT)
