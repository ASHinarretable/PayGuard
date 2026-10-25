"""Minimal .env reader, so keys never have to be exported in a shell."""

from __future__ import annotations

from pathlib import Path


def load_env(path: str | Path = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def read_env_value(path: str | Path, key: str) -> str | None:
    """One value from an env file, without keeping the file's other lines (secrets) in memory."""
    p = Path(path)
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        for line in f:
            name, sep, value = line.partition("=")
            if sep and name.strip() == key and not line.lstrip().startswith("#"):
                return value.strip()
    return None
