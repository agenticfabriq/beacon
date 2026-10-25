# beacon: instructions for Claude Code

This repository is **public** (`agenticfabriq/beacon`). Commits, their history, pull request text and
CI logs are all public, and none of it can be taken back. [CONTRIBUTING.md](CONTRIBUTING.md) covers
setup, the review standard and the conformance contract; this file covers what an agent working here
must not get wrong.

## Changes land through a pull request

Branch from `main`, push the branch, open a pull request, and merge on GitHub. `main` is protected:
a pull request is required (administrators included), and force-pushes and rebase merges are
blocked.

- **Required checks:** `guard`, `lint`, `test` and `typecheck`.
- **CodeQL runs on every pull request without being required.** Let it finish green too. An alert
  needs a decision, a fix or a dismissal with a reason, which no setting can make.
- These are GitHub settings, not files in this repository, so they can drift without a commit. Confirm
  them before relying on them, each command on one line as written here:
  - `gh api repos/agenticfabriq/beacon/branches/main/protection` for the pull-request rule, the required
    checks and the force-push block. It answers admins only: a non-admin gets a plain `Not Found`, where
    an unprotected branch answers `Branch not protected`.
  - `gh api repos/agenticfabriq/beacon --jq '{allow_rebase_merge, allow_squash_merge, allow_merge_commit}'`
    for the merge methods, which the protection call does not report.
  - `gh api repos/agenticfabriq/beacon/code-scanning/default-setup` for CodeQL.

## Run the repo guard before a branch's first push

`scripts/repo-guard.sh` checks for proprietary names, personal identity and secrets.
`git config core.hooksPath .githooks` runs it on staged files before every commit. Before a branch's
first push, run `scripts/repo-guard.sh --all`, as CI does: with no arguments it checks only what is
staged, which before a push is usually nothing.

- The blocklist is `REPO_GUARD_NAME_PATTERNS`: the environment first (CI passes a repo secret), then
  the gitignored `.env`. Without one the guard fails closed.
- A linked worktree with no `.env` of its own uses the main checkout's. Do not copy a `.env` into a
  worktree: it would shadow the main list and go stale.

## What never goes in this repository

- Secrets, `.env` files, and machine paths such as `/Users/<name>/...`.
- Customer data, customer schemas, or the names of their tables and columns.
- Internal specs, plans and strategy. Docs here are written for a public audience.
- Questions or gold SQL from gated benchmarks.
- Details of a security issue before it is fixed. See SECURITY.md.
- A change to `tests/conformance/grading-conformance-v2.json` together with its pin: CONTRIBUTING.md
  says why.
