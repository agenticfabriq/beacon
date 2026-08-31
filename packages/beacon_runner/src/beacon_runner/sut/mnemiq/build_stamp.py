"""Which mnemiq build an in-process SUT is about to run.

``config_identity`` records the decision this exists to satisfy: a build is a
fact about the SYSTEM, so it belongs in ``Solution.version`` and not in a
config knob. Digesting it as config would mislabel a system fact as a knob and
give every repeat of one configuration a new identity -- replicates would stop
pooling into a matrix row and the row's error bar, the spread across its runs,
would silently die. That comment names this module's job as the open gap:
"an in-process SUT hardcodes its VERSION, so two genuinely different engine
builds register as one solution version and DO merge."

Limits worth knowing, because the stamp is only as honest as what it can see:

* a dirty tree reports ``-dirty`` and nothing finer. Two different uncommitted
  trees at one HEAD stamp identically, so the marker says "not reproducible",
  never "this exact content". It is a warning, not an identity.
* only TRACKED modifications count. An untracked stray file in the checkout
  says nothing about the bytes that run, and counting it would split a genuine
  replicate onto a new version.
* it is resolved ONCE PER PROCESS and cached. ``identity()`` is called
  repeatedly -- per sweep arm in ``attribution``, again after the arms in
  ``beacon_ablation.engine``, and in ``registry`` -- and it was a constant
  before this module existed. Re-reading the tree per call would make identity
  non-deterministic: a tracked edit mid-sweep would raise
  ``SutIdentityMismatchError`` on the next arm and abort a sweep that had
  already spent LLM budget on the earlier ones, and the post-sweep read would
  label the Attribution row from a tree nobody compared against. Caching makes
  the stamp describe one process's build, consistently.
* it ANNOTATES; it does not refuse. A dirty tree is not reproducible, and
  deciding whether that should stop a measurement belongs to whoever spends
  the budget -- the ablation driver -- not here.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path


@functools.cache
def build_stamp() -> str | None:
    """Return a short revision marker for the installed mnemiq, or None.

    None where the build cannot be determined -- mnemiq absent, no git, or an
    installed artifact rather than a checkout. Absent is reported as absent:
    a placeholder that looked like a revision would claim a provenance the
    bytes do not have, and an unrecorded build is not a different one.
    """
    try:
        import mnemiq
    except ImportError:
        return None

    git = shutil.which("git")
    if git is None:
        return None
    package = Path(mnemiq.__file__).resolve().parent

    # An INSTALLED artifact is not a checkout of itself, and `git -C <path>`
    # walks UP until it finds a repository -- so a wheel-installed mnemiq in a
    # venv inside another checkout answers with THAT checkout's SHA. A
    # well-formed stamp naming the wrong repo is worse than none: two mnemiq
    # wheels at one host HEAD would merge back into one solution version,
    # which is the thing this module exists to prevent.
    #
    # An ancestor test does not catch it. `--show-toplevel` returns an ancestor
    # of the path whenever git answers at all, so `is_relative_to` is true even
    # for the venv case -- verified against a real repo before relying on it.
    # The install location is the signal that actually separates them.
    if {"site-packages", "dist-packages"} & set(package.parts):
        return None
    root = str(package)

    def _git(*args: str) -> str:
        # Absolute executable, literal argv; `root` comes from the installed
        # package's own location, not from input.
        return subprocess.run(  # noqa: S603
            [git, "-C", root, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    try:
        sha = _git("rev-parse", "--short", "HEAD")
        # --untracked-files=no: a stray file in the checkout is not a change to
        # the code that runs, and flipping identity on one would split
        # replicates that are genuinely the same build.
        dirty = _git("status", "--porcelain", "--untracked-files=no")
    except (subprocess.SubprocessError, OSError):
        return None
    if not sha:
        return None
    return f"{sha}-dirty" if dirty else sha
