from __future__ import annotations

from pathlib import Path

CURRENT_SCHEMA_VERSION = 3
MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def migration_paths() -> list[tuple[int, Path]]:
    paths: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name.split("_", 1)[0])
        paths.append((version, path))
    return paths


def pending_migrations(current_version: int) -> list[tuple[int, Path]]:
    return [(version, path) for version, path in migration_paths() if version > current_version]
