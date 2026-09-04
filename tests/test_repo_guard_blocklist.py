"""The blocklist resolution must not depend on how the secret was quoted (B74).

`repo-guard.sh` reads REPO_GUARD_NAME_PATTERNS from the environment in CI and
from a gitignored `.env` locally. The quote-stripping `sed` ran only in the
`.env` branches, so a secret STORED with `.env`'s surrounding quotes reached
`grep -E` verbatim -- as a pattern beginning with a literal quote, matching
nothing. The guard then reported "clean" on a file it should have blocked, and
did NOT fail closed, because a quoted value is non-empty and passes the
is-it-set check.

That is the worst shape a guard can have: silently inert on a public repo,
reporting the same thing for a clean tree and an unchecked one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "repo-guard.sh"
_TOKEN = "zzqproprietaryname"

# Resolved, not looked up at call time: the guard shells out to `git` and
# `grep` itself, so the PATH handed to it has to be one where those exist.
# A trimmed PATH made the guard's own `git rev-parse` fail silently, which is
# not the environment CI runs it in.
_GIT = shutil.which("git") or "/usr/bin/git"
_BASH = shutil.which("bash") or "/bin/bash"
_PATH = os.environ.get("PATH", "/usr/bin:/bin")


def _repo(tmp_path: Path, *, leak: bool) -> Path:
    subprocess.run([_GIT, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 — resolved path, fixed args
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "repo-guard.sh").write_bytes(_GUARD.read_bytes())
    body = f"mentions {_TOKEN} here\n" if leak else "nothing to see\n"
    (tmp_path / "file.txt").write_text(body, encoding="utf-8")
    subprocess.run([_GIT, "-C", str(tmp_path), "add", "-A"], check=True)  # noqa: S603 — resolved path, fixed args
    return tmp_path


def _run(repo: Path, patterns: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — resolved path, fixed args
        [_BASH, "scripts/repo-guard.sh", "--all"],
        cwd=repo,
        env={"PATH": _PATH, "REPO_GUARD_NAME_PATTERNS": patterns},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "patterns",
    [
        _TOKEN,
        f"'{_TOKEN}'",
        f'"{_TOKEN}"',
    ],
    ids=["bare", "single-quoted", "double-quoted"],
)
def test_a_leak_is_blocked_however_the_blocklist_is_quoted(tmp_path: Path, patterns: str) -> None:
    """The quoted forms are the regression: they reported the leak as clean."""
    result = _run(_repo(tmp_path, leak=True), patterns)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BLOCKED [proprietary-name]" in result.stdout


@pytest.mark.parametrize("patterns", [_TOKEN, f"'{_TOKEN}'"], ids=["bare", "single-quoted"])
def test_a_clean_tree_still_passes(tmp_path: Path, patterns: str) -> None:
    """Stripping quotes must not turn the guard into one that blocks everything."""
    result = _run(_repo(tmp_path, leak=False), patterns)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean" in result.stdout


@pytest.mark.parametrize(
    "patterns",
    ["''", '""', "'", "   ", "*", "[unclosed", ".*", "^", "$"],
    ids=[
        "empty-single",
        "empty-double",
        "lone-quote",
        "whitespace",
        "bare-star",
        "bad-bracket",
        "match-anything",
        "bare-caret",
        "bare-dollar",
    ],
)
def test_a_degenerate_blocklist_fails_CLOSED(tmp_path: Path, patterns: str) -> None:
    """A blocklist that cannot select must refuse, not pass -- on EITHER grep.

    Only `[unclosed` is portably an invalid ERE. `*` is invalid to BSD grep
    (exit 2) and VALID to GNU grep, where it matches every line -- so on CI a
    mangled `*` would have surfaced as BLOCKED [proprietary-name] on every
    file, a phantom leak, while this machine printed the config error. `.*`,
    `^` and `$` are valid to both and match everything.

    Hence the canary probe rather than an exit code: a pattern that matches an
    innocuous fixed string is not a name blocklist, whichever grep is deciding.
    Every one of these has to land in the same place -- blocked, config
    message, exit 1.
    """
    result = _run(_repo(tmp_path, leak=True), patterns)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BLOCKED [config]" in result.stdout


def _write_env(repo: Path, value: str) -> None:
    (repo / ".env").write_text(f"REPO_GUARD_NAME_PATTERNS={value}\n", encoding="utf-8")


def _run_without_env(repo: Path) -> subprocess.CompletedProcess[str]:
    """Force the .env resolution path by supplying no environment value."""
    return subprocess.run(  # noqa: S603 — resolved path, fixed args
        [_BASH, "scripts/repo-guard.sh", "--all"],
        cwd=repo,
        env={"PATH": _PATH},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "stored", [_TOKEN, f"'{_TOKEN}'", f'"{_TOKEN}"'], ids=["bare", "single", "double"]
)
def test_the_dotenv_path_blocks_a_leak_however_it_is_quoted(tmp_path: Path, stored: str) -> None:
    """The .env branch had no test: every other case supplies the value via env.

    Deleting a fallback branch outright, or narrowing `cut -d= -f2-` to `-f2`,
    left all eleven earlier tests green.
    """
    repo = _repo(tmp_path, leak=True)
    _write_env(repo, stored)

    result = _run_without_env(repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BLOCKED [proprietary-name]" in result.stdout


def test_a_dotenv_blocklist_containing_an_EQUALS_is_not_truncated(tmp_path: Path) -> None:
    """`cut -d= -f2-`, not `-f2`.

    An alternation whose own text contains `=` is legal and plausible (a query
    fragment, a config key). Truncating at the first `=` silently narrows what
    the guard blocks, and nothing downstream reports a shortened blocklist --
    the guard still says "clean" for the patterns it lost.
    """
    repo = _repo(tmp_path, leak=True)
    _write_env(repo, f"nomatch=first|{_TOKEN}")

    result = _run_without_env(repo)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BLOCKED [proprietary-name]" in result.stdout


def test_no_blocklist_anywhere_fails_closed(tmp_path: Path) -> None:
    """The founding property: never bless a commit the guard could not check."""
    result = _run_without_env(_repo(tmp_path, leak=True))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BLOCKED [config]" in result.stdout
