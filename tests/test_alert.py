"""Negative tests for the alert path.

The alert path has the same property as the assertions it carries: it is
exercised only on the bad day, so it has to be tested on the good one. Each
test here breaks delivery in one specific way and asserts that runproof
becomes louder rather than quieter.

No network is touched. The transport is replaced with a double that records
what it was asked to send, or raises the failure being tested.
"""
from __future__ import annotations

import smtplib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runproof import alert as alert_mod
from runproof import config as config_mod
from runproof.cli import main
from runproof.model import Report, Result, Status
from runproof.report import to_text

ENV = {
    alert_mod.ENV_HOST: "smtp.example.com",
    alert_mod.ENV_PORT: "587",
    alert_mod.ENV_USER: "robot@example.com",
    alert_mod.ENV_PASSWORD: "app-password",
}

BASE_PROFILE = """
subject = "subject-agent"
database = "{db}"

[schedule]
at = "09:00"
tolerance_minutes = 30
weekdays_only = false

[[assertion]]
id = "A1"
dimension = "liveness"
description = "A run exists"
catches = "Cron removed"
kind = "scalar"
min = {min_runs}
sql = "SELECT COUNT(*) FROM runs"
"""

ALERT_BLOCK = """
[alert]
to = "owner@example.com"
owner = "A Named Human <owner@example.com>"
on = "{on}"
"""


class FakeSMTP:
    """Stands in for `smtplib.SMTP` as a context manager."""

    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def write_profile(tmp: Path, *, alert: str = "", min_runs: int = 1,
                  runs: int = 1) -> Path:
    db = tmp / "subject.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, status TEXT)")
    for _ in range(runs):
        con.execute("INSERT INTO runs (status) VALUES ('ok')")
    con.commit()
    con.close()

    path = tmp / "profile.toml"
    path.write_text(BASE_PROFILE.format(db=db, min_runs=min_runs) + alert)
    return path


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        FakeSMTP.sent = []


class ProfileValidation(TmpCase):
    """A half-configured alert is an error, never a default."""

    def test_alert_without_owner_is_rejected(self):
        path = write_profile(self.tmp, alert="""
[alert]
to = "owner@example.com"
""")
        with self.assertRaises(config_mod.ProfileError) as cm:
            config_mod.load(path)
        self.assertIn("owner", str(cm.exception))

    def test_alert_without_destination_is_rejected(self):
        path = write_profile(self.tmp, alert="""
[alert]
owner = "A Named Human"
""")
        with self.assertRaises(config_mod.ProfileError):
            config_mod.load(path)

    def test_unknown_trigger_is_rejected(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="sometimes"))
        with self.assertRaises(config_mod.ProfileError):
            config_mod.load(path)

    def test_credentials_in_the_profile_are_rejected(self):
        """The profile is committed and shown to prospects."""
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure") + """
password = "hunter2"
""")
        with self.assertRaises(config_mod.ProfileError) as cm:
            config_mod.load(path)
        self.assertIn("credentials", str(cm.exception))


class Delivery(TmpCase):
    def test_missing_transport_env_raises_rather_than_skipping(self):
        config = alert_mod.AlertConfig(to=("owner@example.com",), owner="A Human")
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(alert_mod.AlertError) as cm:
                alert_mod.send(config, "subject", "body")
        self.assertIn(alert_mod.ENV_HOST, str(cm.exception))

    def test_transport_failure_is_an_error_not_a_warning(self):
        config = alert_mod.AlertConfig(to=("owner@example.com",), owner="A Human")
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP",
                               side_effect=smtplib.SMTPAuthenticationError(535, b"nope")):
            with self.assertRaises(alert_mod.AlertError):
                alert_mod.send(config, "subject", "body")

    def test_body_carries_the_failing_assertion(self):
        """The mail has to be actionable without opening the machine."""
        report = Report(subject="subject-agent", generated_at="2026-09-03T09:00:00",
                        owner="A Human")
        from runproof.model import Assertion
        assertion = Assertion("A1", "liveness", "A run exists", "Cron removed")
        report.results.append(Result(assertion, Status.FAIL, "0", ">= 1"))
        body = alert_mod.body_for(report, "profile.toml")
        self.assertIn("A1", body)
        self.assertIn("Cron removed", body)
        self.assertIn("NOT VERIFIABLE", body)


class CliBehaviour(TmpCase):
    def test_failing_check_sends_and_still_exits_one(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"),
                             min_runs=1, runs=0)
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP", FakeSMTP):
            code = main(["check", "--profile", str(path)])
        self.assertEqual(code, 1)
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertEqual(FakeSMTP.sent[0]["To"], "owner@example.com")
        self.assertIn("NOT VERIFIABLE", FakeSMTP.sent[0]["Subject"])

    def test_passing_check_sends_nothing_by_default(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"))
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP", FakeSMTP):
            code = main(["check", "--profile", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(FakeSMTP.sent, [])

    def test_undelivered_alert_exits_three_not_one(self):
        """The distinct exit code is the point: silence must be visible."""
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"),
                             min_runs=1, runs=0)
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP",
                               side_effect=OSError("connection refused")):
            code = main(["check", "--profile", str(path)])
        self.assertEqual(code, 3)

    def test_no_alert_flag_suppresses_delivery(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"),
                             min_runs=1, runs=0)
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP", FakeSMTP):
            code = main(["check", "--profile", str(path), "--no-alert"])
        self.assertEqual(code, 1)
        self.assertEqual(FakeSMTP.sent, [])

    def test_alert_test_proves_the_path(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"))
        with mock.patch.dict("os.environ", ENV, clear=True), \
             mock.patch.object(alert_mod.smtplib, "SMTP", FakeSMTP):
            code = main(["alert-test", "--profile", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_alert_test_without_an_alert_block_is_an_error(self):
        path = write_profile(self.tmp)
        code = main(["alert-test", "--profile", str(path)])
        self.assertEqual(code, 2)


class Rendering(TmpCase):
    def test_unowned_profile_renders_unassigned(self):
        """Missing ownership renders as missing, the way a missing
        assertion renders '–' rather than '✓'."""
        report = Report(subject="subject-agent", generated_at="2026-09-03T09:00:00")
        self.assertIn("UNASSIGNED", to_text(report))

    def test_owned_profile_names_the_human(self):
        path = write_profile(self.tmp, alert=ALERT_BLOCK.format(on="failure"))
        profile = config_mod.load(path)
        self.assertEqual(profile.owner, "A Named Human <owner@example.com>")


if __name__ == "__main__":
    unittest.main()
