"""Environment reading for OneWooz settings.

Deliberately dependency-free: settings must be importable before anything is
pip-installed. Values come from the process environment, optionally seeded from
a ``.env`` file at the repository root.

The reference platform this product replaces shipped secrets with insecure
in-code fallbacks (a Caddy TLS secret was committed to the repo). Nothing here
provides a fallback for a secret: development secrets are *generated* on first
use and written to a gitignored file, production secrets must be present in the
environment or the process refuses to start.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ImproperlyConfigured(Exception):
    """Raised when a required setting is missing. Never caught."""


def load_dotenv(path: Path) -> None:
    """Seed ``os.environ`` from a ``.env`` file.

    Existing environment variables always win, so a real environment (systemd,
    ECS task definition, Secrets Manager injection) is never shadowed by a
    stale local file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def get(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(
            f"{name} must be set in the environment. It has no default by design."
        )
    return value or ""


def get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name}={raw!r} is not a boolean.")


def get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name}={raw!r} is not an integer.") from exc


def get_list(name: str, default: list[str] | None = None) -> list[str]:
    """Read a comma-separated list. Blank entries are dropped."""
    raw = os.environ.get(name)
    if raw is None:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_or_create_dev_secret(name: str, secrets_dir: Path, *, nbytes: int = 48) -> str:
    """Return a development secret, generating and persisting it on first use.

    Used only by the development settings. This keeps two properties that
    matter: no secret is ever committed to the repository, and no human has to
    create a file by hand before the project will run.
    """
    from_env = os.environ.get(name)
    if from_env:
        return from_env

    secrets_dir.mkdir(parents=True, exist_ok=True)
    path = secrets_dir / f"{name.lower()}.key"
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    generated = secrets.token_urlsafe(nbytes)
    path.write_text(generated, encoding="utf-8")
    try:  # best effort; POSIX only
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass
    return generated
