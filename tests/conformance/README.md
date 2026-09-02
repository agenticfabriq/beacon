# Shared grading conformance suite

`grading-conformance-v2.json` is the contract between the two graders in the
portfolio. Verity decides what "correct" means for a question; beacon decides
who is more often correct. If the two disagree about the same answer, neither
number can be cited — and nothing tested that they agreed.

The file is **byte-identical in both repos**:

- `beacon/tests/conformance/grading-conformance-v2.json`
- `semantic-layer-for-ai/crates/grading_pipeline/tests/conformance/grading-conformance-v2.json`

Each repo has a test that pins its SHA-256 -- against its OWN copy, which is
less than it sounds and less than this file used to claim.

| what you do | what happens |
|---|---|
| edit one copy, leave its pin | that repo's suite fails; the other stays green |
| edit one copy **and** its pin, forget the other repo | **both suites pass, with two different contracts** |

The second row is the silent drift the pins are supposed to prevent, and no
test in either repo can see it: each hashes the file beside it against the
literal above it, and neither reads the other copy even with both repos checked
out. Checking out both in CI fixes nothing. The missing comparison is between
the two copies.

No test crosses the gap, so closing it is a human step. There are two routes
and both are sound:

**A. Compare the two copies.** Conclusive on its own, whether or not any suite
has run, because byte-identity across repos is exactly the property and this
answers it directly.

    shasum -a 256 \
      ~/src/fabriq/beacon/tests/conformance/grading-conformance-v2.json \
      ~/src/sandbox/semantic-layer-for-ai/crates/grading_pipeline/tests/conformance/grading-conformance-v2.json

Two digests, two paths, printed. Compare the digests; read the paths to confirm
they are two different files.

`shasum` and not `diff`: `diff` prints nothing on a real match AND nothing when
handed the same file twice, so a mis-paste is indistinguishable from a pass --
the one step that catches cross-repo drift would be the step reporting a false
clean. The subpaths are not symmetric (`tests/conformance/` here,
`crates/grading_pipeline/tests/conformance/` there) and the repos are not
siblings on disk, so both are written in full rather than elided into matching
placeholders that invite pasting one twice. Adjust the roots to wherever the
two repos are checked out; the point is that the output names what it read.

**B. Run both suites, then compare the two pinned literals.** Also conclusive,
by transitivity: each green suite proves its copy matches its own pin, so equal
pins force equal copies. This is the route verity's README prescribes, and it
uses artifacts you may already have from CI.

B is only valid with BOTH suites green. Comparing the literals alone proves
nothing -- edit a copy without touching its pin and the two literals still
match while the files differ.

What is never enough is green suites alone. That is the second row of the table
above: each copy matches its own pin, the two pins differ, and both repos are
green on two different contracts.

To change the contract:

1. Edit both copies.
2. Update the pinned digest in both tests.
3. Run both suites.
4. Close the gap by route A or route B.

Record why in each repo's findings register.
