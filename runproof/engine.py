"""Evaluation: turn assertion specs into Results by querying a SQLite ledger.

Two primitives cover every assertion in AUDIT.md §4, and that is not a
coincidence — a verification question is either "is this number within
bounds" or "does this set of offending rows exist":

    scalar   — a query returning one number, bounded by min / max / equals
    no_rows  — a query that must return nothing; any row returned is a
               violation, and the rows themselves are the diagnostic

There is no third kind, and adding one should require an argument. Neither
primitive calls a model, by design: a check that can be wrong in an
interesting way is not a check.

Provenance: written from a blank file for Smuz. The assertion set it was
built to express is published in `outbound-agent/AUDIT.md` §4, derived from
findings F1–F9 of that audit against source Smuz owns.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .model import Assertion, Result, Status


class LedgerError(RuntimeError):
    """The ledger could not be read at all — a finding in itself."""


class SqliteLedger:
    """Read-only access to an agent's own database.

    Opened read-only on purpose. A verification tool that can write to the
    system it is verifying is a system with one more failure mode.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if not self.path.exists():
            raise LedgerError(f"no database at {self.path}")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def scalar(self, sql: str, params: dict[str, Any]) -> Any:
        con = self._connect()
        try:
            row = con.execute(sql, params).fetchone()
        finally:
            con.close()
        return None if row is None else row[0]

    def rows(self, sql: str, params: dict[str, Any], limit: int = 5) -> list[dict]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchmany(limit)]
        finally:
            con.close()


# --- temporal parameters ----------------------------------------------------

_WEEKDAYS = {0, 1, 2, 3, 4}


def schedule_window(
    *, at: str, tolerance_minutes: int, weekdays_only: bool,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """The window a scheduled run was expected to start in, or None if today
    is not an expected day.

    Returning None is a SKIP, never a PASS. "It wasn't supposed to run today"
    and "it ran correctly" are different answers and a report that conflates
    them is lying by omission.
    """
    now = now or datetime.now(timezone.utc)
    if weekdays_only and now.weekday() not in _WEEKDAYS:
        return None
    hh, mm = (int(x) for x in at.split(":", 1))
    expected = datetime.combine(now.date(), time(hh, mm), tzinfo=timezone.utc)
    delta = timedelta(minutes=tolerance_minutes)
    return expected - delta, expected + delta


def base_params(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return {
        "now": now.isoformat(),
        "today": now.strftime("%Y-%m-%d"),
        "yesterday": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
    }


# --- evaluation -------------------------------------------------------------

def _describe_bounds(spec: dict) -> str:
    parts = []
    if "equals" in spec:
        parts.append(f"= {spec['equals']}")
    if "min" in spec:
        parts.append(f">= {spec['min']}")
    if "max" in spec:
        parts.append(f"<= {spec['max']}")
    return " and ".join(parts) or "any value"


def evaluate(
    assertion: Assertion, spec: dict, ledger: SqliteLedger,
    params: dict[str, Any],
) -> Result:
    kind = spec.get("kind", "scalar")
    sql = spec["sql"]

    try:
        if kind == "scalar":
            value = ledger.scalar(sql, params)
            value = 0 if value is None else value
            failures = []
            if "equals" in spec and value != spec["equals"]:
                failures.append(f"expected {spec['equals']}")
            if "min" in spec and value < spec["min"]:
                failures.append(f"below floor {spec['min']}")
            if "max" in spec and value > spec["max"]:
                failures.append(f"above ceiling {spec['max']}")
            return Result(
                assertion=assertion,
                status=Status.FAIL if failures else Status.PASS,
                observed=str(value),
                expected=_describe_bounds(spec),
                detail="; ".join(failures),
            )

        if kind == "no_rows":
            offenders = ledger.rows(sql, params)
            if not offenders:
                return Result(assertion, Status.PASS, "0 rows", "no rows")
            preview = "; ".join(
                ", ".join(f"{k}={v}" for k, v in row.items()) for row in offenders
            )
            return Result(
                assertion, Status.FAIL, f"{len(offenders)}+ rows", "no rows",
                detail=preview,
            )

        return Result(assertion, Status.ERROR, "-", "-",
                      detail=f"unknown assertion kind: {kind!r}")

    except sqlite3.Error as exc:
        # A check that cannot run is not a check that passed. This is the
        # single most common way monitoring lies.
        return Result(assertion, Status.ERROR, "-", _describe_bounds(spec),
                      detail=f"{type(exc).__name__}: {exc}")
