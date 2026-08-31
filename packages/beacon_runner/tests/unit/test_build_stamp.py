"""What the mnemiq build stamp reports, and what it refuses to claim."""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from beacon_runner.sut.mnemiq import build_stamp as module
from beacon_runner.sut.mnemiq.build_stamp import build_stamp


class _FakeModule:
    __file__ = "/somewhere/mnemiq/__init__.py"


@pytest.fixture(autouse=True)
def _uncached() -> Any:
    """The stamp is cached per process; each test needs its own resolution."""
    build_stamp.cache_clear()
    yield
    build_stamp.cache_clear()


def _installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "mnemiq", _FakeModule())
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")


def _git_returns(monkeypatch: pytest.MonkeyPatch, *, sha: str, dirty: str) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        seen.append(argv)
        out = sha if "rev-parse" in argv else dirty
        return subprocess.CompletedProcess(argv, 0, stdout=out + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_a_clean_checkout_reports_its_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    _git_returns(monkeypatch, sha="30632b9", dirty="")

    assert build_stamp() == "30632b9"


def test_a_dirty_checkout_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare SHA would claim a provenance the bytes do not have."""
    _installed(monkeypatch)
    _git_returns(monkeypatch, sha="30632b9", dirty=" M src/mnemiq/generate/generator.py")

    assert build_stamp() == "30632b9-dirty"


def test_untracked_files_are_not_a_change_to_the_code_that_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting them would split replicates that are genuinely one build.

    A stray file in the checkout says nothing about the bytes that execute,
    so the status probe has to exclude them -- otherwise a leftover dump or
    editor scratch file gives the same build a second solution version.
    """
    _installed(monkeypatch)
    seen = _git_returns(monkeypatch, sha="30632b9", dirty="")

    build_stamp()

    status = next(argv for argv in seen if "status" in argv)
    assert "--untracked-files=no" in status


@pytest.mark.parametrize(
    ("break_it", "why"),
    [
        pytest.param(
            lambda mp: mp.setattr(shutil, "which", lambda _n: None), "no git", id="no git"
        ),
        pytest.param(
            lambda mp: mp.setattr(
                subprocess,
                "run",
                lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(128, "git")),
            ),
            "not a checkout",
            id="not a checkout",
        ),
    ],
)
def test_a_build_that_cannot_be_determined_is_absent_not_invented(
    monkeypatch: pytest.MonkeyPatch, break_it: Any, why: str
) -> None:
    """Unrecorded is not the same as different, so it must not look like one."""
    _installed(monkeypatch)
    _git_returns(monkeypatch, sha="30632b9", dirty="")
    break_it(monkeypatch)

    assert build_stamp() is None, why


def test_mnemiq_absent_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """beacon does not depend on mnemiq; the stamp must not make it one."""
    monkeypatch.setitem(__import__("sys").modules, "mnemiq", None)

    assert build_stamp() is None


def test_the_stamp_module_does_not_import_mnemiq_at_module_scope() -> None:
    """mnemiq is deliberately not a beacon dependency (beacon_runner pyproject).

    Read from the parsed imports rather than the text: a substring check for
    "import mnemiq" misses `from mnemiq import ...`, which would make mnemiq a
    hard import-time dependency just as surely, and a check for "mnemiq" alone
    trips on the module's own docstring.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

    def _module_scope(body: list[ast.stmt]) -> list[ast.stmt]:
        """Statements that run at import time.

        Descends into `try:`/`if:`/`with:`, because an import nested in one is
        still an import-time dependency and scanning `tree.body` alone would
        miss it. Does NOT descend into a def or a class: the lazy
        `import mnemiq` inside `build_stamp` is the whole point, and counting
        it would make this test fail on the correct implementation.
        """
        found: list[ast.stmt] = []
        for node in body:
            found.append(node)
            if isinstance(node, ast.Try):
                found += _module_scope(
                    [*node.body, *node.orelse, *node.finalbody]
                    + [s for handler in node.handlers for s in handler.body]
                )
            elif isinstance(node, ast.If):
                found += _module_scope([*node.body, *node.orelse])
            elif isinstance(node, ast.With):
                found += _module_scope(node.body)
        return found

    imported: list[str] = []
    for node in _module_scope(tree.body):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.split(".")[0] == "mnemiq"], imported


def test_a_wheel_installed_mnemiq_is_not_stamped_with_the_host_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git -C` walks UP, so it answers about whatever repo encloses the path.

    Modelled on the real shape: a wheel inside a venv that itself sits inside
    another checkout. An earlier version of this test paired `/somewhere/mnemiq`
    with a `--show-toplevel` of `/elsewhere`, which git cannot produce -- the
    toplevel is always an ancestor when it succeeds -- so the test was green
    while the case it named went unhandled and the host repo's SHA was
    stamped. The fix keys on the install location instead, so this returns
    before any subprocess runs; git's answer is never reached.
    """
    monkeypatch.setitem(
        __import__("sys").modules,
        "mnemiq",
        type(
            "M",
            (),
            {"__file__": "/host/.venv/lib/python3.13/site-packages/mnemiq/__init__.py"},
        )(),
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")
    seen = _git_returns(monkeypatch, sha="deadbee", dirty="")

    assert build_stamp() is None
    assert seen == [], "should refuse on the path alone, without shelling out"


def test_the_stamp_is_resolved_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """`identity()` is called per sweep arm; re-reading would make it vary.

    A tracked edit mid-sweep would then raise SutIdentityMismatchError on the
    next arm and abandon a sweep that had already spent budget on the earlier
    ones.
    """
    _installed(monkeypatch)
    seen = _git_returns(monkeypatch, sha="30632b9", dirty="")

    first, second = build_stamp(), build_stamp()

    assert first == second == "30632b9"
    assert len([argv for argv in seen if "rev-parse" in argv and "HEAD" in argv]) == 1


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        pytest.param("30632b9", "0.1.0.dev0+30632b9", id="stamped"),
        pytest.param("30632b9-dirty", "0.1.0.dev0+30632b9-dirty", id="dirty"),
        pytest.param(None, "0.1.0.dev0", id="undeterminable"),
    ],
)
def test_the_declared_version_carries_the_build(
    monkeypatch: pytest.MonkeyPatch, stamp: str | None, expected: str
) -> None:
    """This is the one line of product code the change adds.

    `MnemiqInProcessSUT` pinned VERSION as a constant, so two mnemiq builds
    registered as one solution version and merged into one matrix row. The
    stamp is what separates them -- and an undeterminable build falls back to
    the bare constant, because unrecorded is not the same as different.

    Patched in the `in_process` namespace rather than at the source, because
    mnemiq is absent in CI: without this the stamped branch never executes and
    the assertion could not fail.
    """
    from beacon_runner.sut.mnemiq import in_process

    monkeypatch.setattr(in_process, "build_stamp", lambda: stamp)
    sut = in_process.MnemiqInProcessSUT(
        owner_team_id=uuid4(),
        minidev_dir="/nonexistent/minidev",
        bird_dsn="postgresql://nobody@nowhere/none",
        enrich_cache_dir="/nonexistent/cache",
        engine_builder=lambda _db, _enabled: (lambda _q: None, object()),
    )

    assert sut.identity().version == expected
