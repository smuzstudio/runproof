"""Core types for a verification run.

Deliberately contains no SQL, no I/O and no model call. An assertion is a
question with a yes/no answer and a stated consequence; where the answer comes
from is the data source's problem, not this module's.

Provenance: written from a blank file for Smuz. The four dimensions are the
ones set out in the published `outbound-agent/AUDIT.md` §0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Dimension = Literal["liveness", "throughput", "effect", "ownership"]

# Order matters: each dimension is worthless without the one before it. A
# report renders them left to right in this order for that reason.
DIMENSIONS: tuple[Dimension, ...] = ("liveness", "throughput", "effect", "ownership")

DIMENSION_QUESTION: dict[Dimension, str] = {
    "liveness": "Did it run?",
    "throughput": "Did it do work?",
    "effect": "Did the work land?",
    "ownership": "Would anyone know if it hadn't?",
}


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"   # the check itself could not be evaluated
    SKIP = "skip"     # not applicable under the current configuration


@dataclass(frozen=True)
class Assertion:
    """One deterministic check.

    `catches` is not documentation. An assertion that cannot name the failure
    it catches has no reason to exist and should be deleted rather than
    explained — see AUDIT.md §4.
    """

    id: str
    dimension: Dimension
    description: str
    catches: str
    provenance: str = ""


@dataclass
class Result:
    assertion: Assertion
    status: Status
    observed: str
    expected: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (Status.PASS, Status.SKIP)


@dataclass
class Report:
    subject: str
    generated_at: str
    results: list[Result] = field(default_factory=list)
    #: The person who answers a failure here, from the profile's `[alert]`
    #: block. Empty is rendered as "unassigned" rather than omitted — an
    #: unowned check should look unowned on the page, not tidy.
    owner: str = ""

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status in (Status.FAIL, Status.ERROR)]

    @property
    def passed(self) -> bool:
        return not self.failures

    def dimension_status(self, dim: Dimension) -> Status:
        """A dimension is only answered if every assertion under it is.

        Absence of assertions is *not* a pass. An unasked question and a
        satisfied one look identical in a summary table, which is exactly the
        confusion this whole exercise exists to remove.
        """
        rs = [r for r in self.results if r.assertion.dimension == dim]
        if not rs:
            return Status.SKIP
        if any(r.status is Status.ERROR for r in rs):
            return Status.ERROR
        if any(r.status is Status.FAIL for r in rs):
            return Status.FAIL
        return Status.PASS
