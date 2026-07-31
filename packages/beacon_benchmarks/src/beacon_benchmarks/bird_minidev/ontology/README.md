# BIRD Mini-Dev V2 - Ontology authoring guide

Each YAML in this directory is a hand-authored business ontology for one BIRD database.

## Why hand-authored

BIRD ships raw SQLite schemas. Semantic-layer SUTs need business-concept
annotations, such as `eligible_free_rate_k12` as a ratio of two specific
columns, to ground natural-language questions. These annotations cannot be
generated automatically with sufficient quality.

## Authoring procedure per DB

1. Read every BIRD task's `evidence` field for this `db_id`. These are the seed business concepts.
2. Open the SQLite DB locally with `sqlite3 mini_dev_data/dev_databases/<db_id>/<db_id>.sqlite`.
3. List tables and columns; author a table description and per-column `description` plus `business_concept`.
4. For each unique `evidence` expression, author a `business_concepts` entry that captures the formula.
5. Verify that every task's `evidence` can be represented by combining the ontology's `business_concepts` and `tables`. Tasks whose `evidence` cannot be represented are flagged in `metadata.evidence_flag = "unmappable"`.

## Effort

Roughly 2-4 hours per DB. Best done by someone who knows the target semantic
layer well; a Beacon engineer can author with a domain reviewer's sign-off.

## Status

Scaffold YAMLs ship at P6. Full ontology population is a separate content PR
tracked under `BEACON-ONTOLOGY-BIRD-{1..5}`.
