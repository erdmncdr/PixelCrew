#!/usr/bin/env python3
"""Compare the sources in the working folder with the copy inside a .app bundle.

Compared tree: web/, server/ and pixelcrew.py.
Bundle root: <bundle>/Contents/Resources/app
__pycache__ folders and .pyc files are left out.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Dict, List, Optional

SOURCE_DIRS = ("web", "server")
SOURCE_FILES = ("pixelcrew.py",)
BUNDLE_APP = Path("Contents") / "Resources" / "app"
CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def excluded(path: Path) -> bool:
    return path.suffix == ".pyc" or "__pycache__" in path.parts


def collect(root: Path) -> Dict[str, Path]:
    """The compared files under root, keyed by their relative posix path."""
    found: Dict[str, Path] = {}
    for name in SOURCE_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for candidate in base.rglob("*"):
            if candidate.is_file() and not excluded(candidate):
                found[candidate.relative_to(root).as_posix()] = candidate
    for name in SOURCE_FILES:
        path = root / name
        if path.is_file() and not excluded(path):
            found[name] = path
    return found


def missing_sources(root: Path) -> List[str]:
    missing: List[str] = []
    for name in SOURCE_DIRS:
        if not (root / name).is_dir():
            missing.append(name + "/")
    for name in SOURCE_FILES:
        if not (root / name).is_file():
            missing.append(name)
    return missing


def print_list(title: str, paths: List[str]) -> None:
    print(f"{title} ({len(paths)}):")
    for path in paths:
        print(f"  {path}")


def compare(source_root: Path, bundle_root: Path) -> int:
    source = collect(source_root)
    bundle = collect(bundle_root)
    different = sorted(p for p in source.keys() & bundle.keys() if sha256(source[p]) != sha256(bundle[p]))
    missing = sorted(source.keys() - bundle.keys())
    extra = sorted(bundle.keys() - source.keys())

    print(f"Source: {source_root}")
    print(f"Bundle: {bundle_root}")
    print_list("Different", different)
    print_list("Missing", missing)
    print_list("Only in bundle", extra)

    if different or missing or extra:
        print("Result: mismatch")
        return 1
    print("Result: match")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare web/, server/ and pixelcrew.py with the bundle copy by SHA-256."
    )
    parser.add_argument("--bundle", required=True, help="path to the .app bundle")
    options = parser.parse_args(argv)

    bundle = Path(options.bundle)
    if not bundle.is_dir():
        print(f"Error: no bundle at {bundle}", file=sys.stderr)
        return 2

    source_root = Path.cwd()
    missing = missing_sources(source_root)
    if missing:
        print("Error: missing sources: " + ", ".join(missing), file=sys.stderr)
        return 2

    return compare(source_root, bundle / BUNDLE_APP)


if __name__ == "__main__":
    sys.exit(main())
