"""Print the installed consensus-loop version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .update_check import load_version_manifest


def manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "VERSION.json"


def installed_version() -> str:
    return load_version_manifest(manifest_path())["version"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    sys.stdout.write(installed_version() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
