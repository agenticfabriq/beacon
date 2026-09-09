# Contributing to beacon

Beacon tracks how well database-grounded data agents answer questions. It grades pushed output against gold it already holds, and those grades get published — so the most valuable contribution is often not a feature. It is someone re-running a suite and telling us a number is wrong.

## Getting it running

Python 3.11+ and [uv](https://docs.astral.sh/uv/). Postgres comes from Docker.

```bash
make setup      # install the workspace
make db-up      # start Postgres, and create the test database
make migrate    # apply migrations
make test       # the suite
```

`make lint` and `make typecheck` are the other two gates. All three run in CI on every pull request.

**Sync your environment before trusting a local gate.** CI runs `uv sync --all-packages`, and a virtualenv that has drifted from the lock checks something different from what CI checks. This is not hypothetical: a `typecheck` failure reached `main` while `make typecheck` reported *"Success: no issues found in 420 source files"* locally, on the same commit and the same mypy version. Clearing the mypy cache changed nothing; syncing the venv reproduced it immediately. If a local gate disagrees with CI, suspect the venv first.

## Checking a published number

This is the contribution we most want and the one the repository is shaped for.

Every figure beacon reports is derived from run records it holds: the runs, their results, the verdicts, and — where a regrade moved something — an event saying what changed and what it was before. The UI's as-of selector reads the matrix as it stood before a recorded regrade.

It rewinds **grading only**, and the response says so in `as_of.rewinds`. A rate also moves when a run lands or is invalidated, and that is not rewound and has no event behind it. So "this number changed and I cannot find the regrade that did it" is not yet a discrepancy — check the run set first. A number that moved with the run set fixed is the interesting one.

If you re-derive a figure and get something else, that is a report worth filing. Bring the run ids and what you computed. If the difference comes from grading rather than arithmetic, see the note on the conformance contract below, and read [SECURITY.md](SECURITY.md) first — a wrong number is in scope there.

## One file you should not change alone

`tests/conformance/grading-conformance-v2.json` is a **shared contract**. An identical copy lives in another repository, which is private, and both are pinned by hash — `CONTRACT_SHA256` in `tests/test_grading_conformance.py` on this side.

Editing the JSON alone turns beacon's CI red — the pin catches it, which is what the pin is for. The dangerous edit is the one that looks correct: changing the file **and** its pin together. That passes here, because the pin only ever checks the copy sitting beside it, and leaves the two graders disagreeing about what a correct answer is. The other half cannot be done from outside, so:

**Open an issue instead of a pull request** if you believe a conformance case is wrong. Say which case and why. We will make the change on both sides.

Adding a *new* case is a different matter and very welcome — the same route applies, and a case with a concrete gold/output pair attached is close to a finished change.

## The review standard

Pull requests here get read closely, and the standard is narrower than most projects: **say what you measured, not what you expect.**

Concretely, review pushes back on:

- A comment or docstring claiming a guarantee the code does not provide.
- A test whose name or message describes something it does not actually assert. If a guard would pass with the thing it guards removed, it is not a guard.
- A count, bound, or "this happens because X" that has not been checked. Approximations get replaced with the measurement or removed.

None of that is aimed at newcomers — it is applied to everything, including the maintainers' own work, and most of it is caught on our own commits. It is mentioned here so the feedback reads as a house style rather than a hostile reception. If a reviewer says a claim is too strong, the fix is usually to narrow it or delete it, not to argue for it.

The corollary is that "I ran this and here is what happened" is worth far more than a confident description. A finding with a reproduction attached will get further than a well-argued one without.

## Pull requests

Branch, push the branch, open a pull request. CI runs lint, typecheck, the test suite, a repo guard, and CodeQL.

- Keep a pull request to one idea. Single-idea branches get squashed on merge, so the title and description become the commit message — write them as the thing you would want to read in `git log`.
- Commit messages describe what was wrong and how you know. The history is used as a record of decisions, so "fix bug" costs a reviewer more than it saves you.
- If you change behaviour, the test that proves it should fail without the change. Say in the pull request that you checked that, and how.

## Reporting a bug

Open an issue with what you ran, what you expected, and what happened. For anything touching grading correctness, credentials, or one tenant's data reaching another, use [SECURITY.md](SECURITY.md)'s private route instead.
