"""Helper de env vars (patron portado del repo de demos CFO).

La demo corre entera sin API keys ni red: este modulo solo resuelve
metadatos de la corrida (commit hash) y lecturas opcionales de entorno.
"""

from __future__ import annotations

import os
import subprocess


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Lee una variable de entorno; si required y falta, falla explicito."""
    value = os.environ.get(name, default)
    if required and value is None:
        raise RuntimeError(f"Variable de entorno requerida ausente: {name}")
    return value


def resolve_commit() -> str:
    """Commit hash para el audit trail: env GIT_COMMIT con fallback a git local."""
    commit = os.environ.get("GIT_COMMIT")
    if commit:
        return commit.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or "sin-git"
    except Exception:
        return "sin-git"
