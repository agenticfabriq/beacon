#!/usr/bin/env bash
# repo-guard: block commits that leak proprietary names, personal identity, or
# secrets into this public-track repo. Runs as a pre-commit hook (--staged) and
# in CI (--all).
#
# The proprietary-name blocklist is NOT in this file or this repo: it comes from
# REPO_GUARD_NAME_PATTERNS -- set in the environment (CI passes a repo secret)
# or in the gitignored .env. A public guard must not carry what it blocks.
# Without it the guard fails closed rather than blessing an unchecked commit.
set -uo pipefail

MODE="${1:---staged}"

list_files() {
  if [ "$MODE" = "--all" ]; then
    git ls-files
  else
    git diff --cached --name-only --diff-filter=ACM
  fi
}

# Resolve the blocklist: environment first (CI), then the gitignored .env, then
# the main checkout's .env for a linked worktree (which has none of its own --
# .env is gitignored and does not carry across). The fallbacks widen where the
# secret is FOUND, never what happens without one.
#
# ONE resolution path, and the quote handling is why. It used to be stripped
# only in the .env branches, so a secret STORED with .env's surrounding quotes
# reached grep as a pattern beginning with a literal quote and matched nothing.
# The guard then printed "clean" on a file it should have blocked, and did not
# fail closed, because a quoted value is non-empty and passes the is-it-set
# check. Reproduced with a synthetic token: bare blocked, `'token'` reported
# clean. Two code paths for one value is what let the env one drift.
read_patterns_from() {
  grep '^REPO_GUARD_NAME_PATTERNS=' "$1" 2>/dev/null | head -1 | cut -d= -f2-
}

RAW="${REPO_GUARD_NAME_PATTERNS:-}"
if [ -z "$RAW" ] && [ -f .env ]; then
  RAW="$(read_patterns_from .env)"
fi
if [ -z "$RAW" ]; then
  MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")"
  if [ -n "$MAIN_ROOT" ] && [ -f "$MAIN_ROOT/.env" ]; then
    RAW="$(read_patterns_from "$MAIN_ROOT/.env")"
  fi
fi
if [ -z "$RAW" ]; then
  echo "repo-guard: BLOCKED [config]: REPO_GUARD_NAME_PATTERNS is not set."
  echo "Set it in .env (gitignored) or the environment; in CI it comes from a repo secret."
  echo "The guard fails closed: it will not bless a commit it could not check."
  exit 1
fi

# Strip ONE matching leading/trailing quote pair, whatever the source.
NAME_PATTERNS="$(printf '%s' "$RAW" | sed -e "s/^\(['\"]\)\(.*\)\1\$/\2/")"

# A blocklist that cannot express a pattern must refuse, not pass. Each of
# these reported "clean" before, which is the same output as a checked clean
# tree -- the one thing a guard must never be ambiguous about.
guard_config_failure() {
  echo "repo-guard: BLOCKED [config]: $1"
  echo "REPO_GUARD_NAME_PATTERNS must be an extended regular expression, optionally"
  echo "wrapped in one matching pair of quotes. The guard fails closed rather than"
  echo "reporting a tree it could not actually check."
  exit 1
}
if ! printf '%s' "$NAME_PATTERNS" | grep -q '[^[:space:]]'; then
  guard_config_failure "the blocklist is empty (or only whitespace) after quote stripping."
fi
case "$NAME_PATTERNS" in
  \'*|\"*|*\'|*\")
    # An unmatched quote survived, so the stored value is mangled. Left alone it
    # either matches nothing or matches every apostrophe in the tree; neither is
    # a name blocklist.
    guard_config_failure "the blocklist still begins or ends with a quote: the stored value is mangled."
    ;;
esac
# ONE probe against a string no real blocklist can contain, read three ways.
# The exit code alone is not portable and the difference is not academic:
#
#   pattern      BSD grep 2.6 (macOS /usr/bin/grep)   GNU grep 3.12 (CI)
#   *            2 (invalid)                          0 (matches every line)
#   [unclosed    2 (invalid)                          2 (invalid)
#   .*  ^  $     0 (valid, matches everything)        0 (same)
#
# So on CI a mangled `*` blocklist would have surfaced as BLOCKED
# [proprietary-name] on EVERY file -- a phantom leak to chase -- while this
# machine reported the config error the check was added to emit. Measured in an
# ubuntu:latest container against the staged script, not reasoned about.
#
#   0  the pattern matches innocuous text, so it is not a name blocklist
#   1  valid and selective: the only outcome that proceeds
#  >1  grep rejected the expression
#
# The scan below sends grep's stderr to /dev/null, so without this an invalid
# or over-broad blocklist passed or blocked everything in silence.
GUARD_CANARY='zzq-repo-guard-canary-no-real-blocklist-matches-this-9418'
printf '%s\n' "$GUARD_CANARY" | grep -qEi "$NAME_PATTERNS" >/dev/null 2>&1
case "$?" in
  0)
    guard_config_failure "the blocklist matches arbitrary text, so it would block every file."
    ;;
  1) ;;
  *)
    guard_config_failure "the blocklist is not a valid extended regular expression."
    ;;
esac

# Secret shapes. Never commit these.
SECRET_PATTERNS='(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{8,})'

# Hardcoded machine home paths leak the local username. Use $HOME / os.path.expanduser("~")
# or an env var (e.g. BEACON_DATA_DIR) instead. Never a literal /Users/<name> or /home/<name>.
HOME_PATH_PATTERNS='/(Users|home)/[A-Za-z0-9._-]+'

# The scanner itself: free of name tokens now, but its secret-shape patterns
# can match their own spelling. Never scan it.
EXCLUDE_REGEX='^scripts/repo-guard\.sh$'

fail=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if printf '%s\n' "$f" | grep -qE "$EXCLUDE_REGEX"; then continue; fi
  [ -f "$f" ] || continue

  if grep -InEi "$NAME_PATTERNS" "$f" >/dev/null 2>&1; then
    echo "BLOCKED [proprietary-name]: $f"
    grep -InEi "$NAME_PATTERNS" "$f" | head -3 || true
    fail=1
  fi
  if grep -InE "$SECRET_PATTERNS" "$f" >/dev/null 2>&1; then
    echo "BLOCKED [secret]: $f (matched a secret pattern)"
    fail=1
  fi
  if grep -InE "$HOME_PATH_PATTERNS" "$f" >/dev/null 2>&1; then
    echo "BLOCKED [home-path]: $f (hardcodes a machine home directory)"
    grep -InE "$HOME_PATH_PATTERNS" "$f" | head -3 || true
    fail=1
  fi
done < <(list_files)

if [ "$fail" -ne 0 ]; then
  echo
  echo "repo-guard: commit blocked. Remove the offending content (or route secrets/URLs through"
  echo "env vars and a gitignored .env)."
  exit 1
fi
echo "repo-guard: clean."
