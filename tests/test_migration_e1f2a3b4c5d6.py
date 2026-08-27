"""Tests for the fold-forward logic in alembic migration e1f2a3b4c5d6
(replace client_version_records with client_version_status).

This migration already shipped one real bug found via manual review
(commit be5907b, fixed to leave main_updated_at NULL when stale) and a
second one fixed alongside these tests (using updated_at instead of
created_at to pick the "latest overall" main snapshot across
environments) — neither caught by any automated test before now.

Rather than invoke Alembic's upgrade() directly (its DDL/insert calls go
through `op`, which needs a live MigrationContext and isn't a natural fit
for a fast unit test), this imports the migration module by file path and
exercises `_fold_client_history()` — the pure function upgrade() delegates
its per-client fold-forward decision-making to. That function contains
100% of the logic these bugs lived in (which row is "last", how
*_previous_version is derived, which environment's main snapshot wins,
when main_updated_at is populated vs left NULL) with no Alembic/DB
dependency, so testing it directly is a real test of the bug-prone logic
without contorting around `op`.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "e1f2a3b4c5d6_replace_client_version_records_with_status.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_e1f2a3b4c5d6", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


def _row(*, current_version, main_version=None, main_pr_number=None,
         recorded_by=1, deployment_request_id=1, created_at, updated_at=None):
    return SimpleNamespace(
        current_version=current_version,
        main_version=main_version,
        main_pr_number=main_pr_number,
        recorded_by=recorded_by,
        deployment_request_id=deployment_request_id,
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )


def _dt(day, hour=0):
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def test_fold_test_only_history_rolls_previous_version_forward():
    records_by_env = {
        "test": [
            _row(current_version="1.0", created_at=_dt(1)),
            _row(current_version="1.1", created_at=_dt(2)),
        ],
        "live": [],
    }

    values = migration._fold_client_history(records_by_env, cached_version=None, cached_version_changed_at=None)

    assert values["test_current_version"] == "1.1"
    assert values["test_previous_version"] == "1.0"
    assert "live_current_version" not in values


def test_fold_live_only_single_row_leaves_previous_version_none():
    records_by_env = {
        "test": [],
        "live": [_row(current_version="2.0", created_at=_dt(1))],
    }

    values = migration._fold_client_history(records_by_env, cached_version=None, cached_version_changed_at=None)

    assert values["live_current_version"] == "2.0"
    assert values["live_previous_version"] is None
    assert "test_current_version" not in values


def test_fold_picks_main_snapshot_by_created_at_not_updated_at():
    # The test-environment row is the most recent by created_at (deploy
    # actually happened later) but has an OLDER updated_at bump than the
    # live-environment row (which was edited/corrected after the fact,
    # bumping its updated_at without it being the more recent real deploy).
    # The fix (finding #4) requires comparing created_at, so the winning
    # main snapshot must come from the test row, not the live row.
    records_by_env = {
        "test": [
            _row(
                current_version="1.0", main_version="main-A", main_pr_number=10,
                created_at=_dt(5), updated_at=_dt(5, 1),
            ),
        ],
        "live": [
            _row(
                current_version="2.0", main_version="main-B", main_pr_number=20,
                created_at=_dt(3), updated_at=_dt(9),
            ),
        ],
    }

    values = migration._fold_client_history(records_by_env, cached_version=None, cached_version_changed_at=None)

    assert values["main_version"] == "main-A"
    assert values["main_pr_number"] == 10


def test_fold_main_updated_at_populated_when_matches_cache():
    cache_time = _dt(10)
    records_by_env = {
        "test": [_row(current_version="1.0", main_version="cached-version", created_at=_dt(1))],
        "live": [],
    }

    values = migration._fold_client_history(
        records_by_env, cached_version="cached-version", cached_version_changed_at=cache_time
    )

    assert values["main_updated_at"] == cache_time


def test_fold_main_updated_at_null_when_stale_vs_cache():
    records_by_env = {
        "test": [_row(current_version="1.0", main_version="old-version", created_at=_dt(1))],
        "live": [],
    }

    values = migration._fold_client_history(
        records_by_env, cached_version="new-version", cached_version_changed_at=_dt(10)
    )

    assert values["main_updated_at"] is None


def test_fold_handles_no_cache_row_at_all():
    """Reproduces the scenario behind finding #1: a fresh/never-synced
    database has zero rows in bitbucket_main_branch_status, so upgrade()
    now passes cached_version=None/cached_version_changed_at=None (via
    `.first() or (None, None)` instead of `.one()`). Folding must not
    blow up, and a client with no folded main_version at all correctly
    gets a NULL main_updated_at (not accidentally matching None == None)."""
    records_by_env = {"test": [], "live": []}

    values = migration._fold_client_history(records_by_env, cached_version=None, cached_version_changed_at=None)

    assert values["main_version"] is None
    assert values["main_updated_at"] is None
