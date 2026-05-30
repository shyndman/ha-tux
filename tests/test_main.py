import os
from pathlib import Path
import re
import subprocess
import sys


SOURCE_PATH = Path(__file__).resolve().parents[1] / "src"


def test_module_entrypoint_emits_structured_log() -> None:
    python_path = os.fspath(SOURCE_PATH)
    completed = subprocess.run(
        [sys.executable, "-m", "ha_tux"],
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
    assert completed.stdout == ""
    assert completed.stderr
    assert re.search(r'"event"\s*:\s*".+"', completed.stderr)
    assert re.search(r'"logger"\s*:\s*"ha_tux"', completed.stderr)
    assert re.search(r'"level"\s*:\s*"info"', completed.stderr)
