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

**B. Run both suites on the CURRENT tree, then compare the two pinned
literals.** Also conclusive, by transitivity: each green suite proves its copy
matches its own pin, so equal pins force equal copies.

    grep CONTRACT_SHA256 \
      ~/src/fabriq/beacon/tests/test_grading_conformance.py \
      ~/src/sandbox/semantic-layer-for-ai/crates/grading_pipeline/tests/grading_conformance.rs

Same mis-paste hazard as route A, and worse: the two constants share a name AND
a value, so reading one repo's literal twice looks exactly like a match. `grep`
over both paths prints the path beside each hit.

Two conditions, and skipping either makes B prove nothing:

- **Both suites green.** Literals alone say nothing -- edit a copy without
  touching its pin and the literals still match while the files differ. That is
  row 1 of the table.
- **Green on the tree in front of you.** A remembered CI run attests to the
  commit it ran on. Route A reads the files as they are now; route B is only as
  fresh as the run you are citing.

Verity's README prescribes route B but its change-the-contract recipe omits the
suite run, which is the condition that makes it valid. Run the suites.

What is never enough is green suites alone. That is the second row of the table
above: each copy matches its own pin, the two pins differ, and both repos are
green on two different contracts.

To change the contract:

1. Edit both copies.
2. Update the pinned digest in both tests.
3. Run both suites.
4. Close the gap by route A or route B.

Record why in each repo's findings register.

Kinds, with examples rather than a partition — 24 cases, and no attempt here
to assign every one. The line that used to stand here said "three kinds,
matching the contract's own `purpose` field", and it survived the first attempt
at this section, sitting directly above four bold headings.

**Real disagreements**, where the two graders differed on the same answer.
`num-representation-noise` is the 45 correct answers beacon was calling wrong;
`text-different` is the 84 value mismatches that were never a tolerance
question; `table-numeric-cell-within-tolerance` is verity applying a curated
tolerance to a scalar and ignoring it for the same number in a table.

**Bounds one of them had wrong.** `num-whole-off-by-one` caught the defect in
beacon's relative bound the day it was added; `num-within-widened-rel` and
`num-beyond-widened-rel` sit either side of the v2 widening.

**Agreement baselines that must not regress** — `match: true`, and no episode
behind them. `num-exact` ("identical values match under any tolerance"),
`text-identical`, `table-identical`, `num-whole-equal` and
`table-null-matches-null` ("both graders agree the answer is empty").

`text-different` is NOT one of these, though an earlier pass moved it here. It
is `match: false`, and its `why` carries an episode -- "84 of beacon's value
mismatches were this" -- so it fails both halves of the qualifier above. It
stays under real disagreements, where it was.

**Type distinctions that must never coerce** — `match: false` baselines
asserting things stay DIFFERENT: `table-null-is-not-zero`,
`table-null-is-not-empty-text`, `table-bool-is-not-one` and
`table-number-is-not-its-spelling`. Not "neither episodes nor agreements", as
this section first claimed: `table-bool-is-not-one` failed on its first run,
because Python says `True == 1` and beacon's equality fallback would have
scored a boolean answer against a count (f7af576). That is an episode, and the
same kind of one used two paragraphs up to file `num-whole-off-by-one`.

That fourth kind is why the `purpose` field no longer says "three kinds": it
named the type-discipline cases in the sentence right after claiming three,
contradicting itself inside one string. The kind before it is why the field
says "include" rather than counting — the older wording, "every case came from
a real disagreement", was false for many of them, and replacing one count with
another is how that survives.

No number is given here on purpose, and the purpose was earned. This paragraph
has carried two counts and both were wrong: "a third of the file", then "nine
of the twenty-four", which was only the size of the two groups named above
while at least eight further cases -- `num-different-answer`, `num-near-zero`,
`table-extra-column`, `table-row-count`, the row-order cases,
`table-whole-off-by-one` -- record neither a grader disagreement nor an
episode. Three attempts, three wrong counts, in the paragraph arguing that
loose counts are the failure mode. The section above says it plainly: examples,
not a partition, and no attempt to assign every case.
