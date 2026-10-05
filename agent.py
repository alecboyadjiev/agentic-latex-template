from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from metasrc.cli import main


WRAPPER_ARGS_FILE_ENV = "AGENT_WRAPPER_ARGS_FILE"


def _restore_wrapper_arguments() -> None:
    path_value = os.environ.pop(WRAPPER_ARGS_FILE_ENV, None)
    if path_value is None:
        return
    if len(sys.argv) != 1:
        raise ValueError("Forwarded wrapper arguments cannot be combined with command-line arguments.")
    try:
        values = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read forwarded wrapper arguments: {exc}") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("Forwarded wrapper arguments must be a JSON array of strings.")
    sys.argv.extend(values)


if __name__ == "__main__":
    try:
        _restore_wrapper_arguments()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main())
