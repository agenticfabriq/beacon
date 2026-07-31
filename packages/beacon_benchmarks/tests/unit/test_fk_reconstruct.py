"""FK reconstruction from SQLite naming conventions."""

from __future__ import annotations

from beacon_benchmarks.ingest.fk_reconstruct import (
    ForeignKeyEdge,
    detect_implicit_fks,
    emit_alter_constraints,
)


def test_detect_implicit_fk_by_column_name_match() -> None:
    schema = {
        "schools": ["id", "name"],
        "frpm": ["schools_id", "value"],
    }
    edges = detect_implicit_fks(schema)
    assert (
        ForeignKeyEdge(
            from_table="frpm",
            from_col="schools_id",
            to_table="schools",
            to_col="id",
        )
        in edges
    )


def test_detect_implicit_fk_by_shared_pk_name() -> None:
    schema = {
        "schools": ["CDSCode", "County"],
        "frpm": ["CDSCode", "FreeMealCountK12"],
    }
    edges = detect_implicit_fks(schema)
    assert (
        ForeignKeyEdge(
            from_table="frpm",
            from_col="CDSCode",
            to_table="schools",
            to_col="CDSCode",
        )
        in edges
    )


def test_no_fk_when_no_shared_columns() -> None:
    schema = {"a": ["x"], "b": ["y"]}
    assert detect_implicit_fks(schema) == []


def test_emit_alter_constraints() -> None:
    edges = [
        ForeignKeyEdge("frpm", "CDSCode", "schools", "CDSCode"),
        ForeignKeyEdge("frpm", "schools_id", "schools", "id"),
    ]
    stmts = emit_alter_constraints(edges)
    assert any("ALTER TABLE frpm" in stmt for stmt in stmts)
    assert any("REFERENCES schools" in stmt for stmt in stmts)
    assert any("FOREIGN KEY (cdscode)" in stmt for stmt in stmts)
    assert all(stmt.endswith(";") for stmt in stmts)
