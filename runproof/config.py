"""Load a verification profile from TOML.

The profile is the whole interface. Pointing runproof at a new agent means
writing one of these — not writing Python — which is what makes the same
tool usable on someone else's infrastructure without them handing over
read access to anything but their own database.

Provenance: written from a blank file for Smuz.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .alert import AlertConfig
from .model import DIMENSIONS, Assertion


@dataclass
class Schedule:
    at: str = "09:00"
    tolerance_minutes: int = 30
    weekdays_only: bool = True


@dataclass
class Profile:
    subject: str
    database: Path
    schedule: Schedule = field(default_factory=Schedule)
    params: dict[str, Any] = field(default_factory=dict)
    assertions: list[tuple[Assertion, dict]] = field(default_factory=list)
    alert: AlertConfig | None = None

    @property
    def owner(self) -> str:
        return self.alert.owner if self.alert else ""


class ProfileError(ValueError):
    pass


def _load_alert(raw: dict, path: Path) -> AlertConfig | None:
    """Parse the optional `[alert]` block.

    Absent, the profile is a check with no destination — legitimate while
    developing one, and reported as unowned rather than quietly fine.
    Present but malformed is an error: a half-configured alert is the
    failure mode this block exists to remove.
    """
    block = raw.get("alert")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ProfileError(f"{path}: [alert] must be a table")

    to = block.get("to")
    if isinstance(to, str):
        to = [to]
    if not to or not all(isinstance(x, str) and x.strip() for x in to):
        raise ProfileError(f"{path}: [alert] needs 'to' — one address or a list")

    owner = str(block.get("owner", "")).strip()
    if not owner:
        # Same rule as `catches` on an assertion: if it cannot name the
        # human, it is not ownership.
        raise ProfileError(
            f"{path}: [alert] needs 'owner' — the name of the person who "
            f"answers this alert, not a team or a mailbox"
        )

    on = str(block.get("on", "failure"))
    if on not in ("failure", "always"):
        raise ProfileError(
            f"{path}: [alert] on={on!r} (expected 'failure' or 'always')"
        )

    for secret in ("password", "smtp_password", "api_key", "token"):
        if secret in block:
            raise ProfileError(
                f"{path}: [alert] must not contain {secret!r}; transport "
                f"credentials come from the environment"
            )

    return AlertConfig(
        to=tuple(x.strip() for x in to),
        owner=owner,
        subject_prefix=str(block.get("subject_prefix", "[runproof]")),
        on=on,  # type: ignore[arg-type]
        from_addr=str(block.get("from", "")),
        timeout_seconds=float(block.get("timeout_seconds", 20.0)),
    )


def load(path: str | Path) -> Profile:
    path = Path(path).expanduser().resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc

    for key in ("subject", "database"):
        if key not in raw:
            raise ProfileError(f"{path}: missing required key {key!r}")

    # Database paths are resolved relative to the profile, so a profile and
    # the agent it verifies can move together.
    db = Path(str(raw["database"])).expanduser()
    if not db.is_absolute():
        db = (path.parent / db).resolve()

    sched = Schedule(**raw.get("schedule", {}))
    specs = raw.get("assertion", [])
    if not specs:
        raise ProfileError(f"{path}: no assertions defined")

    assertions: list[tuple[Assertion, dict]] = []
    seen: set[str] = set()
    for spec in specs:
        for key in ("id", "dimension", "description", "catches", "sql"):
            if key not in spec:
                raise ProfileError(f"{path}: assertion missing {key!r}: {spec!r}")
        if spec["dimension"] not in DIMENSIONS:
            raise ProfileError(
                f"{path}: assertion {spec['id']}: unknown dimension "
                f"{spec['dimension']!r} (expected one of {', '.join(DIMENSIONS)})"
            )
        if spec["id"] in seen:
            raise ProfileError(f"{path}: duplicate assertion id {spec['id']!r}")
        seen.add(spec["id"])
        assertions.append((
            Assertion(
                id=spec["id"],
                dimension=spec["dimension"],
                description=spec["description"],
                catches=spec["catches"],
                provenance=spec.get("provenance", ""),
            ),
            spec,
        ))

    return Profile(
        subject=str(raw["subject"]),
        database=db,
        schedule=sched,
        params=raw.get("params", {}),
        assertions=assertions,
        alert=_load_alert(raw, path),
    )
