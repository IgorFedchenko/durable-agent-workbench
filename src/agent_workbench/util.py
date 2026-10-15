"""Canonical serialization and fingerprints; no credentials or user data in logs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "datasets").is_dir():
    ROOT = Path(__file__).resolve().parent / "assets"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def object_hash(value: Any) -> str:
    return digest(canonical(value).encode())


def file_hash(path: Path) -> str:
    return digest(path.read_bytes())


def source_fingerprint() -> str:
    paths = sorted(
        p
        for folder in ("src", "sandbox")
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    if not paths:
        paths = sorted(
            p
            for p in Path(__file__).parent.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
        return object_hash({str(p.relative_to(Path(__file__).parent)): file_hash(p) for p in paths})
    paths.extend(ROOT / name for name in ("pyproject.toml", "uv.lock") if (ROOT / name).exists())
    return object_hash({str(p.relative_to(ROOT)): file_hash(p) for p in paths})
