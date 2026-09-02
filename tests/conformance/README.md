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

Detection is therefore two steps, not one:

1. Run both suites -- ties each copy to its own pin.
2. Compare the two copies across repos -- ties the two repos together.

Step 2 is a human protocol, and comparing the two JSON FILES is the direct way
to do it: byte-identity across repos is the property, and a file comparison
answers it outright. Comparing the two literals only implies that property when
step 1 has already passed in both repos -- edit a copy without its pin and the
two literals still match while the files differ.

    shasum -a 256 \
      ~/src/fabriq/beacon/tests/conformance/grading-conformance-v2.json \
      ~/src/sandbox/semantic-layer-for-ai/crates/grading_pipeline/tests/conformance/grading-conformance-v2.json

Two digests, two paths, printed. Compare the digests; read the paths to confirm
they are two different files.

`shasum` and not `diff`, for the reason this whole section exists. `diff`
prints nothing on a real match AND nothing when handed the same file twice, so
a mis-paste is indistinguishable from a pass -- the one step that catches
cross-repo drift would be the step reporting a false clean. The subpaths are
not symmetric (`tests/conformance/` here, `crates/grading_pipeline/tests/conformance/`
there) and the repos are not siblings on disk, so both are written in full
rather than elided into matching placeholders that invite pasting one twice.
Adjust the roots to wherever the two repos are checked out; the point is that
the output names what it read.

To change the contract, four steps. Step 3 is what makes step 4 conclusive:
with both suites green, each copy provably matches its own pin, so comparing
the copies settles byte-identity across repos.

1. Edit both copies.
2. Update the pinned digest in both tests.
3. Run both suites.
4. `shasum` both copies and compare, as above.

Record why in each repo's findings register.

Every case came from a real disagreement or from a bound one of the two graders
had wrong. `num-representation-noise` is the 45 correct answers beacon was
calling wrong; `table-numeric-cell-within-tolerance` is verity applying a
curated tolerance to a scalar and ignoring it for the same number in a table.
