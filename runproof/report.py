"""Rendering. Two audiences, one set of facts.

The summary table is what a CTO reads; the per-assertion detail is the
evidence behind it. Both come from the same Report, so they cannot drift.

Provenance: the summary shape follows `outbound-agent/AUDIT.md` §1, written
by Smuz. Rendering code written from a blank file.
"""
from __future__ import annotations

from .model import DIMENSION_QUESTION, DIMENSIONS, Report, Status

_MARK = {
    Status.PASS: "ok",
    Status.FAIL: "FAIL",
    Status.ERROR: "ERROR",
    Status.SKIP: "n/a",
}
_GLYPH = {Status.PASS: "✓", Status.FAIL: "✗", Status.ERROR: "!", Status.SKIP: "–"}


def to_text(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"runproof · {report.subject} · {report.generated_at}")
    lines.append(f"  owner: {report.owner or 'UNASSIGNED — no alert destination'}")
    lines.append("")

    verdict = "VERIFIED" if report.passed else "NOT VERIFIABLE"
    cells = " ".join(
        f"{dim[:4]}={_GLYPH[report.dimension_status(dim)]}" for dim in DIMENSIONS
    )
    lines.append(f"  {cells}   →  {verdict}")
    lines.append("")

    for result in report.results:
        a = result.assertion
        lines.append(f"  [{_MARK[result.status]:>5}] {a.id}  {a.description}")
        if result.status is not Status.PASS:
            lines.append(f"          observed: {result.observed}   expected: {result.expected}")
            if result.detail:
                lines.append(f"          {result.detail}")
            lines.append(f"          catches: {a.catches}")
    lines.append("")
    if report.passed:
        lines.append("  All assertions hold.")
    else:
        lines.append(f"  {len(report.failures)} assertion(s) not satisfied.")
    return "\n".join(lines)


def to_markdown(report: Report) -> str:
    """The deliverable form — pasteable into an audit or a client email."""
    out: list[str] = [
        f"# Verification report — `{report.subject}`",
        "",
        f"**Generated:** {report.generated_at}  ",
        f"**Owner:** {report.owner or '_unassigned — no alert destination_'}  ",
        f"**Verdict:** {'Verified' if report.passed else 'Not verifiable'}",
        "",
        "| Dimension | Question | Result |",
        "|---|---|---|",
    ]
    for dim in DIMENSIONS:
        out.append(
            f"| {dim.title()} | {DIMENSION_QUESTION[dim]} | "
            f"{_GLYPH[report.dimension_status(dim)]} |"
        )
    out += ["", "| # | Assertion | Observed | Expected | Result |", "|---|---|---|---|---|"]
    for r in report.results:
        out.append(
            f"| {r.assertion.id} | {r.assertion.description} | `{r.observed}` | "
            f"`{r.expected}` | {_GLYPH[r.status]} |"
        )

    failures = report.failures
    if failures:
        out += ["", "## Not satisfied", ""]
        for r in failures:
            out += [
                f"### {r.assertion.id} — {r.assertion.description}",
                "",
                f"- **Observed:** `{r.observed}` (expected `{r.expected}`)",
                f"- **Catches:** {r.assertion.catches}",
            ]
            if r.detail:
                out.append(f"- **Detail:** {r.detail}")
            if r.assertion.provenance:
                out.append(f"- **Derives from:** {r.assertion.provenance}")
            out.append("")
    return "\n".join(out)
