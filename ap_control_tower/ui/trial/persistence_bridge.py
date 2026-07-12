"""Puente tolerante entre Streamlit Trial y PostgreSQL."""

from __future__ import annotations

from functools import lru_cache

from ...persistence import is_persistence_available


@lru_cache(maxsize=1)
def _engine():
    from ...persistence.session import build_engine
    return build_engine()


def available() -> bool:
    return is_persistence_available()


def save(trial_session) -> None:
    if not available():
        return
    from ...persistence.session import session_scope
    from ...persistence.trial_repository import save_trial_session
    with session_scope(_engine()) as db:
        save_trial_session(db, trial_session)


def list_runs(limit: int = 25) -> list[dict]:
    if not available():
        return []
    from ...persistence.session import session_scope
    from ...persistence.trial_repository import list_trial_runs
    with session_scope(_engine()) as db:
        return list_trial_runs(db, limit=limit)


def load(run_id: str):
    if not available():
        return None
    from ...persistence.session import session_scope
    from ...persistence.trial_repository import load_trial_run
    with session_scope(_engine()) as db:
        return load_trial_run(db, run_id)


def delete(run_id: str) -> bool:
    if not available():
        return False
    from ...persistence.session import session_scope
    from ...persistence.trial_repository import delete_trial_run
    with session_scope(_engine()) as db:
        return delete_trial_run(db, run_id)
