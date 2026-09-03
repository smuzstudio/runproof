# runproof

Deterministic verification that a scheduled agent actually ran.

Not monitoring, and not evals. Those answer *"is the output any good?"*
This answers a prior question that most agent stacks cannot answer at all:

| | Question | Why it's separate |
|---|---|---|
| **Liveness** | Did it run? | A perfect agent that isn't firing is a dead agent. |
| **Throughput** | Did it do work? | A run that processes zero records exits successfully and looks identical to a healthy one. |
| **Effect** | Did the work land? | "The API call returned 200" is not "the thing happened." |
| **Ownership** | Would anyone know if it hadn't? | An unowned failure is an indefinite failure. |

An agent passes only if all four are answerable **from data, by a check
containing no model.** There is no LLM anywhere in this tool. A check that
can be wrong in an interesting way is not a check.

## The failure it exists for

Your agent runs on a cron. One morning its upstream source changes a JSON
schema and starts returning an empty list. The agent discovers nothing,
processes nothing, prints a polite summary and **exits 0**. Cron records
success. This repeats every weekday.

There is no crash, no stack trace, no alert. You find out weeks later when
you notice the numbers dropped, and you will first suspect your prompt.

That is not an exception. It is success with an empty payload, and nothing
in a normal observability stack is looking for it.

## Run it

Zero dependencies beyond the Python standard library, and it opens your
database **read-only**. A verification tool that can write to the system it
verifies is a system with one more failure mode.

```bash
python -m runproof check --profile runproof/profiles/outbound.toml
```

```
runproof · outbound-agent · 2026-08-31T09:22:16+00:00
  owner: Nick Lysenko <hello@smuz.io>

  live=✓ thro=✓ effe=✗ owne=✓   →  NOT VERIFIABLE

  [   ok] A1  A run started inside today's expected window
  [ FAIL] A7  Every real send today carries a provider message id
          observed: 1+ rows   expected: no rows
          id=1, to_email=f0@acme.com, provider=resend
          catches: Silent transport degradation; bounce investigation becoming impossible
  [ FAIL] A8  No address has ever been emailed twice
          observed: 1+ rows   expected: no rows
          email=f0@acme.com, times=2
          catches: Dedupe regression — the only failure here a recipient can see
```

`--markdown` emits a report you can paste into a document; `--out FILE`
writes it. The exit code is the contract:

| | |
|---|---|
| `0` | every assertion held |
| `1` | at least one did not (the alert, if configured, was sent) |
| `2` | the profile or the database could not be read at all |
| `3` | a check ran but its result could not be delivered to its owner |

## Point it at your own agent

Write a profile. No Python required — the TOML *is* the interface.

```toml
subject  = "nightly-reconciler"
database = "/var/lib/reconciler/state.db"

[schedule]
at = "02:00"
tolerance_minutes = 30
weekdays_only = false

[[assertion]]
id          = "A1"
dimension   = "liveness"
description = "A run started inside last night's window"
catches     = "Cron removed, host asleep, credentials expired"
kind        = "scalar"
uses_schedule = true
min = 1
sql = "SELECT COUNT(*) FROM runs WHERE started_at BETWEEN :window_start AND :window_end"
```

**`catches` is required, and it is not documentation.** An assertion that
cannot name the failure it catches has no reason to exist and should be
deleted rather than explained.

### The two primitives

Every assertion is one of two shapes. There is no third, and adding one
should require an argument.

- **`scalar`** — a query returning one number, bounded by `min` / `max` /
  `equals`.
- **`no_rows`** — a query that must return nothing. Any row returned is a
  violation, and the rows themselves are printed as the diagnostic.

### Parameters available in SQL

| | |
|---|---|
| `:now`, `:today`, `:yesterday` | current UTC instant and dates |
| `:window_start`, `:window_end` | the expected start window, when `uses_schedule = true` |
| `:since` | now minus `window_hours`, when the assertion sets it |
| anything under `[params]` | your own constants — caps, ceilings, sentinel values |

Set bounds in the profile, **not** by reading the agent's own config. A cap
that verifies itself against its own configuration cannot detect a
misconfigured cap.

## Dimensions with no assertions are not passes

A dimension you asked no questions about renders as `–`, never `✓`. An
unasked question and a satisfied one look identical in most dashboards, and
that confusion is the thing this tool exists to remove.

Likewise, a check that *errors* — a missing table, a renamed column — is
reported as `ERROR`, never as a pass. Silent check failure is the single most
common way monitoring lies.

## Where a failure goes

The first three questions are answered from data. The fourth — *would anyone
know?* — is not a property of the data at all, and no query can establish it.
So the profile names a destination and a human:

```toml
[alert]
to      = "hello@example.com"
owner   = "A Named Human <hello@example.com>"
on      = "failure"          # or "always", for a daily heartbeat
```

Credentials never appear in the profile — it is committed, and in this
project it is published. The transport reads four environment variables, and
their **absence is an error, not a silent skip**:

```bash
export RUNPROOF_SMTP_HOST=smtp.gmail.com
export RUNPROOF_SMTP_PORT=587
export RUNPROOF_SMTP_USER=you@example.com
export RUNPROOF_SMTP_PASSWORD=…      # an app password, not the account password
```

Three rules hold this together, and none of them is decoration:

1. **A failed delivery exits 3.** Not 0, and not folded into 1. An alerter
   that fails quietly converts a loud failure into a silent one, which is
   the exact substitution this tool exists to prevent.
2. **`owner` is required whenever `[alert]` is present**, for the same
   reason `catches` is required on an assertion. A destination with no named
   human is a mailbox everyone assumes someone else reads. With no `[alert]`
   at all, the report renders its owner as `UNASSIGNED` rather than omitting
   the line.
3. **The alert path is tested on the good day.** A path that only ever runs
   when something is broken is untested every other day, and an expired app
   password is discovered at the worst possible moment. `alert-test` sends a
   message on demand and belongs on its own schedule:

```bash
python -m runproof alert-test --profile runproof/profiles/outbound.toml
```

```cron
# the check, every weekday morning, half an hour after the agent
30 9  * * 1-5  cd ~/runproof && ./.venv/bin/python -m runproof check -p runproof/profiles/outbound.toml
# and a weekly proof that the alert path itself still works
0  9  * * 1    cd ~/runproof && ./.venv/bin/python -m runproof alert-test -p runproof/profiles/outbound.toml
```

Note what this does **not** claim. The `[alert]` block is configuration, so
no assertion reports ownership as `✓` on the strength of the block existing —
that would be a check verifying itself against its own configuration, which
is the thing this project refuses to do everywhere else. Ownership is
established by the weekly `alert-test` actually arriving, and by a human
noticing when it stops.

## What this is not

- **Not evals.** It says nothing about whether the output was any good.
  Output quality is an evaluation problem; whether the job happened is a
  verification problem. Conflating them is how monitoring gets sold badly.
- **Not a scheduler, and not a fixer.** It observes; it never writes.
- **Not APM.** No traces, no spans, no agent instrumentation. It reads a
  database your agent already writes to.
- **Not zero-setup.** If your agent keeps no record of its own runs, there is
  nothing to verify, and that absence is your first finding.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Every assertion has a fixture that breaks it *specifically*. A verification
tool tested only against healthy data is worth nothing — the entire point is
its behaviour on the bad day, and the bad day is the one you cannot rehearse
in production.

## Provenance

See [PROVENANCE.md](PROVENANCE.md). Every file was written from an empty
file; the assertion set derives from a published audit of the author's own
code. Nothing here originates in any client engagement.
