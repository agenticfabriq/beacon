# Shared grading conformance suite

`grading-conformance-v2.json` is the contract between the two graders in the
portfolio. Verity decides what "correct" means for a question; beacon decides
who is more often correct. If the two disagree about the same answer, neither
number can be cited — and nothing tested that they agreed.

The file is **byte-identical in both repos**:

- `beacon/tests/conformance/grading-conformance-v2.json`
- `semantic-layer-for-ai/crates/grading_pipeline/tests/conformance/grading-conformance-v2.json`

Each repo has a test that pins its SHA-256. Editing one copy and not the other
fails both suites, which is the point: a shared contract that can drift silently
is not shared.

To change the contract: edit both copies, update the pinned digest in both
tests, and record why in each repo's findings register.

Every case came from a real disagreement or from a bound one of the two graders
had wrong. `num-representation-noise` is the 45 correct answers beacon was
calling wrong; `table-numeric-cell-within-tolerance` is verity applying a
curated tolerance to a scalar and ignoring it for the same number in a table.
