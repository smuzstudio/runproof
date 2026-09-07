# Provenance

This file records where each part of `runproof` came from. It exists because
the author has prior and current consulting engagements whose agreements
restrict the *use* — not merely the disclosure — of methods and procedures
developed under them. A clean-room record written as the code is written is
cheap; reconstructing one later, under question, is not.

**Summary: every file in this repository was written from an empty file for
Smuz, on personal equipment and personal time, and derives only from the
sources named below.**

| Component | Origin |
|---|---|
| `runproof/model.py` | Written from a blank file. The four dimensions (liveness, throughput, effect, ownership) are as set out in `outbound-agent/AUDIT.md` §0, a document written by the author about the author's own code. |
| `runproof/engine.py` | Written from a blank file. The two-primitive design (`scalar`, `no_rows`) was derived by reducing the ten assertions in that audit's §4 to their minimal common shape. |
| `runproof/config.py` | Written from a blank file. Standard TOML loading. |
| `runproof/report.py` | Written from a blank file. The summary-table shape follows `AUDIT.md` §1. |
| `runproof/alert.py` | Written from a blank file. Standard `smtplib` and `email.message`. The delivery rules (a failed send is fatal and distinctly coded; credentials never in the profile; the path is proven on the good day) were derived from finding F5 of `AUDIT.md`, written by the author about the author's own code. |
| `runproof/cli.py` | Written from a blank file. Standard `argparse`. |
| `runproof/profiles/outbound.toml` | Written from a blank file. Each assertion traces to a numbered finding (F1–F10) in `AUDIT.md`, which was produced by reading `outbound-agent`, a repository the author owns outright. A12 added 7 Sep 2026 for F10 (unhonoured opt-outs), from the same source. |
| `tests/` | Written from a blank file against the schema in this repository. |

## What this repository deliberately does not contain

- No code, configuration, schema, or naming taken from any client system.
- No client-specific failure modes, incident histories, thresholds, or metrics.
- No operating procedure, runbook, or checklist developed under any engagement.
- No dependency on any private package or internal service.

The assertions here are derived from failure modes that are general to
scheduled software — a job that did not fire, a run that processed nothing, a
side effect that did not land, a failure nobody was told about. They are
stated in the audit as consequences of reading this project's own source, and
each one is traceable to that reading.

## Dependencies

The library depends on the Python standard library only (`sqlite3`,
`tomllib`, `argparse`, `datetime`, `smtplib`). This is a deliberate constraint, not an
accident of scope: a verification tool that a stranger is asked to run
against their own production database has to be readable end to end in one
sitting, and has to add no supply chain to the system it is checking.
