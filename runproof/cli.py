"""`runproof check --profile <file>` and `runproof alert-test`.

Exits non-zero when any assertion fails, so cron and CI can treat it as a
normal failing command. When the profile declares an `[alert]` block the
failing report is also mailed to the human named there, because an exit
code on a machine nobody is watching is not ownership — it is the same
silence in a different colour.

    0  every assertion held
    1  at least one assertion did not hold (the alert, if any, was sent)
    2  the profile or the subject database could not be read at all
    3  a check ran but its result could not be delivered to its owner

Exit 3 is deliberately distinct and deliberately loud. An undelivered alert
converts a loud failure into a silent one, which is the precise substitution
this tool exists to prevent, so it is never folded into exit 0 or 1.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import alert as alert_mod
from . import config as config_mod
from .engine import LedgerError, SqliteLedger, base_params, evaluate, schedule_window
from .model import Report, Result, Status
from .report import to_markdown, to_text


def _run_profile(profile: config_mod.Profile, now: datetime) -> Report:
    ledger = SqliteLedger(profile.database)
    params = base_params(now) | dict(profile.params)

    window = schedule_window(
        at=profile.schedule.at,
        tolerance_minutes=profile.schedule.tolerance_minutes,
        weekdays_only=profile.schedule.weekdays_only,
        now=now,
    )
    if window is not None:
        params["window_start"], params["window_end"] = (w.isoformat() for w in window)

    report = Report(
        subject=profile.subject,
        generated_at=now.isoformat(timespec="seconds"),
        owner=profile.owner,
    )
    for assertion, spec in profile.assertions:
        if spec.get("uses_schedule") and window is None:
            # Not an expected run day. Skipped, never passed.
            report.results.append(Result(
                assertion, Status.SKIP, "not a scheduled day", "-",
                detail="no run expected today",
            ))
            continue
        local = params
        if "window_hours" in spec:
            # A lookback window expressed in the profile rather than in SQL,
            # so date arithmetic stays in Python where the timezone is known.
            local = params | {
                "since": (now - timedelta(hours=float(spec["window_hours"]))).isoformat()
            }
        report.results.append(evaluate(assertion, spec, ledger, local))
    return report


def _check(args) -> int:
    try:
        profile = config_mod.load(args.profile)
        report = _run_profile(profile, datetime.now(timezone.utc))
    except (config_mod.ProfileError, LedgerError) as exc:
        print(f"runproof: {exc}", file=sys.stderr)
        return 2

    rendered = to_markdown(report) if args.markdown else to_text(report)
    if args.out:
        args.out.write_text(rendered + "\n")
        print(f"wrote {args.out}")
    else:
        print(rendered)

    status = 0 if report.passed else 1

    if profile.alert is None or args.no_alert:
        return status
    if not alert_mod.should_alert(profile.alert, report):
        return status

    try:
        destination = alert_mod.deliver(profile.alert, report, str(args.profile))
    except alert_mod.AlertError as exc:
        # The check itself may have passed; delivery failing still matters,
        # because the next one that fails will fail the same way.
        print(f"runproof: alert not delivered: {exc}", file=sys.stderr)
        return 3
    print(f"runproof: alerted {destination}", file=sys.stderr)
    return status


def _alert_test(args) -> int:
    """Prove the alert path end to end, on a day when nothing is broken.

    An alert path that only ever runs on the bad day is untested on every
    other day, and the bad day is the worst moment to discover an expired
    app password. This belongs on its own schedule alongside the check.
    """
    try:
        profile = config_mod.load(args.profile)
    except config_mod.ProfileError as exc:
        print(f"runproof: {exc}", file=sys.stderr)
        return 2
    if profile.alert is None:
        print(f"runproof: {args.profile} declares no [alert] block", file=sys.stderr)
        return 2

    try:
        destination = alert_mod.send(
            profile.alert,
            f"{profile.subject}: alert path test",
            "This is runproof testing its own alert path. No assertion was "
            "evaluated and nothing is wrong.\n\n"
            f"subject: {profile.subject}\n"
            f"owner:   {profile.alert.owner}\n"
            f"profile: {args.profile}\n\n"
            "If this message stops arriving on its schedule, the alert path "
            "is broken and every check below it has gone quiet.",
        )
    except alert_mod.AlertError as exc:
        print(f"runproof: alert not delivered: {exc}", file=sys.stderr)
        return 3
    print(f"runproof: test alert delivered to {destination}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runproof")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run a profile's assertions")
    check.add_argument("--profile", "-p", required=True, type=Path)
    check.add_argument("--markdown", "-m", action="store_true",
                       help="emit the report as markdown instead of text")
    check.add_argument("--out", "-o", type=Path, help="write the report to a file")
    check.add_argument("--no-alert", action="store_true",
                       help="evaluate and print, but send nothing (local runs)")

    test = sub.add_parser("alert-test",
                          help="send a test message to the profile's alert destination")
    test.add_argument("--profile", "-p", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "alert-test":
        return _alert_test(args)
    return _check(args)


if __name__ == "__main__":
    sys.exit(main())
