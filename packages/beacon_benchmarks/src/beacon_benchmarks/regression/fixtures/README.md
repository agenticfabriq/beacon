# Regression fixtures

One JSONL file per benchmark, named `<adapter>_sample.jsonl`. Each fixture
captures up to 100 task, prediction, and baseline score triples. The expected
pass@1 for each packaged fixture is recorded in
`tests/regression/<adapter>/expected.json`.

The packaged rows are hand-curated offline validation baselines generated from
`beacon-offline-reference-v1`. They validate that Beacon's adapter/grader
bindings replay the recorded outcomes consistently without requiring network
downloads, live judge services, or external SQL engines. They are not external
leaderboard or published-paper performance claims.

Future fixtures may use published reference predictions from benchmark authors.
When that happens, set `source_type` to `published` in the matching
`expected.json` and document the source artifact there.

## How to regenerate

Run:

```console
uv run beacon benchmarks make-baseline-fixture <name> \
  --sut built-in --max-tasks 100 \
  --output packages/beacon_benchmarks/src/beacon_benchmarks/regression/fixtures/<name>_sample.jsonl
```

Cover: `DABStep`, `InsightBench`, `DRBench`, `DSBenchDA`,
`DSBenchDM`, `FDABench`, `Text2Vis`, `BIRD`, `Spider2Lite`.

The generator emits five deterministic rows per fixture by default. When
published reference predictions or larger hand-curated SUT snapshots are
available, replace or extend the generated rows while preserving the JSONL
schema and updating the corresponding `expected.json` manifest.

## Update cadence

Re-export fixtures whenever:

- Beacon's grader implementation changes in a way that could change outcomes.
- The benchmark source dataset version changes.
- A discrepancy is reported by `tests/integration/test_regression_*.py`.
