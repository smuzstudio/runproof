"""Negative tests: each assertion must fail on the failure it claims to catch.

A verification tool tested only against healthy data is worth nothing — the
whole point is behaviour on the bad day, and the bad day is the one you
cannot rehearse in production. So every assertion here gets a fixture that
breaks it specifically.

Schema is inlined rather than imported from the agent, so runproof stays
dependency-free and testable on its own.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runproof import config as config_mod
from runproof.cli import _run_profile
from runproof.model import Status

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "runproof" / "profiles" / "outbound.toml"

SCHEMA = """
CREATE TABLE leads (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, source_ref TEXT,
  company_name TEXT, company_url TEXT, company_domain TEXT, founder_name TEXT,
  founder_email TEXT, one_liner TEXT, research_notes TEXT, status TEXT DEFAULT 'new',
  skip_reason TEXT, created_at TEXT, run_id INTEGER);
CREATE TABLE sends (id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, to_email TEXT,
  subject TEXT, body TEXT, provider TEXT, provider_message_id TEXT, sent_at TEXT,
  run_id INTEGER, status TEXT DEFAULT 'sent', error TEXT);
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  source TEXT, requested_n INTEGER, dry_run INTEGER, daily_cap INTEGER,
  status TEXT DEFAULT 'running', cost_usd REAL, error TEXT);
CREATE TABLE suppressions (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, domain TEXT,
  reason TEXT, source TEXT, note TEXT, active INTEGER DEFAULT 1, created_at TEXT);
"""

# A Monday, so the weekdays_only schedule always applies. Fixing "now" keeps
# the suite deterministic: a test that passes only on Tuesdays is a flake.
NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")


class Fixture:
    """A healthy outbound.db, which each test then damages in one way."""

    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        self.add_run()
        self.add_lead(1, "acme.com", sent=True)
        self.add_lead(2, "beta.io", sent=True)
        self.add_lead(3, "ceres.dev", sent=False)
        self.con.commit()

    def suppress(self, *, email=None, domain=None, reason="unsubscribe",
                 created=None) -> None:
        self.con.execute(
            "INSERT INTO suppressions (email, domain, reason, active, created_at)"
            " VALUES (?, ?, ?, 1, ?)",
            (email, domain, reason, created or "2026-08-30T09:00:00+00:00"),
        )
        self.con.commit()

    def add_run(self, *, run_id=1, dry_run=0, status="ok", cost=0.42,
                started=None, finished=None) -> None:
        self.con.execute(
            "INSERT INTO runs (id, started_at, finished_at, source, requested_n,"
            " dry_run, daily_cap, status, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, started or f"{TODAY}T09:05:00+00:00",
             finished or f"{TODAY}T09:12:00+00:00", "yc", 3, dry_run, 15, status, cost),
        )

    def add_lead(self, i, domain, *, sent, run_id=1, provider="resend",
                 msg_id="msg", email=None) -> None:
        email = email or f"f{i}@{domain}"
        self.con.execute(
            "INSERT INTO leads (id, source, source_ref, company_name, company_domain,"
            " founder_email, status, created_at, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (i, "yc", f"s{i}", f"Co{i}", domain, email,
             "sent" if sent else "skipped", f"{TODAY}T09:06:00+00:00", run_id),
        )
        if sent:
            self.add_send(i, email, provider=provider, msg_id=f"{msg_id}-{i}", run_id=run_id)

    def add_send(self, lead_id, email, *, provider="resend", msg_id="m", run_id=1,
                 sent_at=None, status="sent") -> None:
        self.con.execute(
            "INSERT INTO sends (lead_id, to_email, subject, body, provider,"
            " provider_message_id, sent_at, run_id, status)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (lead_id, email, "s", "b", provider, msg_id,
             sent_at or f"{TODAY}T09:07:00+00:00", run_id, status),
        )

    def sql(self, statement, *args) -> None:
        self.con.execute(statement, args)
        self.con.commit()


class ProfileTest(unittest.TestCase):
    def run_check(self, damage=None):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "outbound.db"
            fx = Fixture(db)
            if damage:
                damage(fx)
            fx.con.commit()
            fx.con.close()

            profile = config_mod.load(PROFILE)
            profile.database = db
            report = _run_profile(profile, NOW)
        return {r.assertion.id: r for r in report.results}

    def assert_only_failing(self, results, expected_ids):
        failing = {i for i, r in results.items() if r.status is not Status.PASS}
        self.assertEqual(failing, set(expected_ids),
                         msg="\n".join(f"{i}: {r.status.value} — {r.detail}"
                                       for i, r in sorted(results.items())))

    # --- healthy baseline ---------------------------------------------------

    def test_healthy_passes_everything(self):
        self.assert_only_failing(self.run_check(), set())

    # --- liveness -----------------------------------------------------------

    def test_run_never_happened(self):
        # Cron didn't fire. The database is otherwise untouched — which is
        # exactly why this is invisible without a run ledger.
        r = self.run_check(lambda fx: fx.sql("DELETE FROM runs"))
        self.assert_only_failing(r, {"A1", "A2", "A3", "A10"})

    def test_run_started_but_crashed(self):
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE runs SET finished_at = NULL, status = 'running'"))
        self.assert_only_failing(r, {"A2", "A10"})

    def test_run_outside_expected_window(self):
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE runs SET started_at = ?", f"{TODAY}T14:00:00+00:00"))
        self.assert_only_failing(r, {"A1", "A2", "A3"})

    def test_last_success_is_stale(self):
        old = (NOW - timedelta(hours=48)).isoformat()
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE runs SET started_at = ?, finished_at = ?", old, old))
        self.assert_only_failing(r, {"A1", "A2", "A3", "A10"})

    # --- throughput ---------------------------------------------------------

    def test_zero_work_run_exits_ok(self):
        # The failure this whole product exists for: success with an empty
        # payload. Nothing crashed; the upstream source just went quiet.
        def damage(fx):
            fx.sql("DELETE FROM sends")
            fx.sql("DELETE FROM leads")
        self.assert_only_failing(self.run_check(damage), {"A3"})

    def test_lead_abandoned_mid_run(self):
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE leads SET status = 'researched' WHERE id = 3"))
        self.assert_only_failing(r, {"A4"})

    # --- effect -------------------------------------------------------------

    def test_cap_exceeded(self):
        def damage(fx):
            for n in range(20):
                fx.add_send(1, f"extra{n}@x.com", msg_id=f"x{n}")
        self.assert_only_failing(self.run_check(damage), {"A5"})

    def test_real_send_from_a_dry_run(self):
        r = self.run_check(lambda fx: fx.sql("UPDATE runs SET dry_run = 1"))
        self.assert_only_failing(r, {"A6"})

    def test_missing_provider_message_id(self):
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE sends SET provider_message_id = NULL WHERE lead_id = 1"))
        self.assert_only_failing(r, {"A7"})

    def test_address_emailed_twice(self):
        # The only failure in this set that the recipient can see.
        r = self.run_check(lambda fx: fx.add_send(1, "f1@acme.com", msg_id="dup"))
        self.assert_only_failing(r, {"A8"})

    def test_optout_recorded_but_still_emailed(self):
        # F10: the footer promises removal. A suppression row that exists while
        # the sends kept going is that promise being broken, and the recipient
        # is the one who finds out.
        r = self.run_check(lambda fx: fx.suppress(email="f1@acme.com"))
        self.assert_only_failing(r, {"A12"})

    def test_optout_at_one_address_covers_the_domain(self):
        r = self.run_check(lambda fx: fx.suppress(domain="beta.io"))
        self.assert_only_failing(r, {"A12"})

    def test_send_before_the_optout_is_not_a_violation(self):
        # Suppression is not retroactive: it forbids the next email, not the
        # one that prompted the reply. Without this the assertion would fire
        # on every honoured opt-out and be turned off within a week.
        r = self.run_check(lambda fx: fx.suppress(
            email="f1@acme.com", created="2099-01-01T00:00:00+00:00"))
        self.assert_only_failing(r, set())

    def test_send_left_awaiting_outcome(self):
        # The process died between claiming the slot and confirming the
        # transport result. The row is the only evidence that happened.
        r = self.run_check(lambda fx: fx.sql(
            "UPDATE sends SET status = 'reserved', provider_message_id = NULL"
            " WHERE lead_id = 1"))
        self.assert_only_failing(r, {"A11"})

    def test_released_send_is_not_contact(self):
        # A transport that positively rejected the message must not look like
        # a delivered one: it neither breaches the cap nor counts as a
        # duplicate, and it is not expected to carry a message id.
        def damage(fx):
            fx.add_send(1, "f0@acme.com", provider="resend", msg_id=None,
                        status="released")
        self.assert_only_failing(self.run_check(damage), set())

    # --- ownership ----------------------------------------------------------

    def test_runaway_cost(self):
        r = self.run_check(lambda fx: fx.sql("UPDATE runs SET cost_usd = 9.99"))
        self.assert_only_failing(r, {"A9"})

    # --- the tool's own failure modes ---------------------------------------

    def test_missing_table_errors_rather_than_passes(self):
        r = self.run_check(lambda fx: fx.sql("DROP TABLE runs"))
        self.assertTrue(any(x.status is Status.ERROR for x in r.values()))
        self.assertFalse(any(x.status is Status.PASS and x.assertion.id in {"A1", "A2"}
                             for x in r.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
