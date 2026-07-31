"""Detect implicit foreign-key relationships from column-name conventions."""

from __future__ import annotations

from dataclasses import dataclass

from beacon_benchmarks.ingest.schema_translator import quote_identifier


@dataclass(frozen=True)
class ForeignKeyEdge:
    """A reconstructed foreign-key edge between two loaded tables."""

    from_table: str
    from_col: str
    to_table: str
    to_col: str


def detect_implicit_fks(schema: dict[str, list[str]]) -> list[ForeignKeyEdge]:
    """Return FK edges inferred from ``{table: [columns]}``, sorted deterministically."""
    edges: list[ForeignKeyEdge] = []
    tables = sorted(schema)

    for child in tables:
        for col in schema[child]:
            if not col.endswith("_id"):
                continue
            base = col[:-3]
            for parent in (base, f"{base}s"):
                if parent != child and parent in schema and "id" in schema[parent]:
                    edges.append(ForeignKeyEdge(child, col, parent, "id"))
                    break

    for child in tables:
        for col in schema[child]:
            if col.endswith("_id") or col.lower() == "id":
                continue
            for parent in tables:
                if parent == child or col not in schema[parent]:
                    continue
                root = col.lower().rstrip("s")
                root_matches_parent = root in parent.lower() and root not in child.lower()
                plural_parent = parent.endswith("s") and not child.endswith("s")
                if root_matches_parent or plural_parent:
                    edges.append(ForeignKeyEdge(child, col, parent, col))

    seen: set[ForeignKeyEdge] = set()
    out: list[ForeignKeyEdge] = []
    for edge in sorted(edges, key=lambda edge: (edge.from_table, edge.from_col)):
        if edge in seen:
            continue
        seen.add(edge)
        out.append(edge)
    return out


def emit_alter_constraints(edges: list[ForeignKeyEdge]) -> list[str]:
    """Emit ``ALTER TABLE ... ADD CONSTRAINT`` statements for FK edges."""
    stmts = []
    for edge in edges:
        name = f"fk_{edge.from_table}_{edge.from_col}__{edge.to_table}".lower()
        stmts.append(
            f"ALTER TABLE {quote_identifier(edge.from_table)} "
            f'ADD CONSTRAINT "{name}" '
            f"FOREIGN KEY ({quote_identifier(edge.from_col)}) "
            f"REFERENCES {quote_identifier(edge.to_table)}({quote_identifier(edge.to_col)});"
        )
    return stmts
