"""Schema translation from SQLite CREATE TABLE to PostgreSQL CREATE TABLE."""

from __future__ import annotations

from beacon_benchmarks.ingest.schema_translator import (
    quote_identifier,
    requote_identifier,
    translate_create_table,
    translate_type,
)


def test_translate_type_basic() -> None:
    assert translate_type("INTEGER") == "BIGINT"
    assert translate_type("INT") == "BIGINT"
    assert translate_type("TEXT") == "TEXT"
    assert translate_type("REAL") == "DOUBLE PRECISION"
    assert translate_type("NUMERIC") == "NUMERIC"
    assert translate_type("DATETIME") == "TIMESTAMP"
    assert translate_type("BOOLEAN") == "BOOLEAN"
    assert translate_type("BLOB") == "BYTEA"


def test_translate_type_with_size_preserves() -> None:
    assert translate_type("VARCHAR(255)") == "VARCHAR(255)"
    assert translate_type("NUMERIC(10,2)") == "NUMERIC(10,2)"


def test_translate_type_case_insensitive() -> None:
    assert translate_type("integer") == "BIGINT"
    assert translate_type("Real") == "DOUBLE PRECISION"


def test_requote_identifier_backtick_to_double_quote() -> None:
    assert requote_identifier("`Free Meal Count (K-12)`") == '"Free Meal Count (K-12)"'
    assert requote_identifier("`CDSCode`") == '"CDSCode"'


def test_requote_identifier_passthrough_unquoted() -> None:
    assert requote_identifier("CDSCode") == "CDSCode"


def test_quote_identifier_lowercases_simple_identifiers() -> None:
    assert quote_identifier("CDSCode") == "cdscode"
    assert quote_identifier("`buildUpPlaySpeed`") == "buildupplayspeed"
    assert quote_identifier("Team_Attributes") == "team_attributes"


def test_quote_identifier_quotes_postgres_reserved_words() -> None:
    assert quote_identifier("cross") == '"cross"'


def test_quote_identifier_preserves_special_identifiers() -> None:
    assert quote_identifier("`Free Meal Count (K-12)`") == '"Free Meal Count (K-12)"'


def test_translate_create_table_simple() -> None:
    ddl = "CREATE TABLE schools (CDSCode TEXT PRIMARY KEY, County TEXT, EnrollK12 INTEGER)"
    out = translate_create_table(ddl)
    assert "CREATE TABLE schools" in out
    assert "cdscode TEXT" in out
    assert "county TEXT" in out
    assert "enrollk12 BIGINT" in out
    assert "TEXT" in out
    assert "BIGINT" in out
    assert "PRIMARY KEY" in out


def test_translate_create_table_with_quoted_columns() -> None:
    ddl = (
        "CREATE TABLE frpm ("
        "`CDSCode` TEXT, "
        "`Free Meal Count (K-12)` INTEGER, "
        "`Enrollment (K-12)` REAL"
        ")"
    )
    out = translate_create_table(ddl)
    assert "cdscode TEXT" in out
    assert '"Free Meal Count (K-12)"' in out
    assert '"Enrollment (K-12)"' in out
    assert "DOUBLE PRECISION" in out
    assert "`" not in out


def test_translate_create_table_omits_table_fk_constraints_for_bulk_load() -> None:
    ddl = (
        "CREATE TABLE frpm (`CDSCode` TEXT, FOREIGN KEY (`CDSCode`) REFERENCES schools(`CDSCode`))"
    )
    out = translate_create_table(ddl)
    assert "cdscode" in out
    assert "FOREIGN KEY" not in out
    assert "REFERENCES" not in out


def test_translate_create_table_omits_table_primary_key_constraints_for_bulk_load() -> None:
    ddl = (
        "CREATE TABLE lapTimes ("
        "raceId INTEGER not null, "
        "driverId INTEGER not null, "
        "lap INTEGER not null, "
        "primary key (raceId, driverId, lap)"
        ")"
    )
    out = translate_create_table(ddl)
    assert "raceid BIGINT not null" in out
    assert "driverid BIGINT not null" in out
    assert "lap BIGINT not null" in out
    assert '"primary" KEY' not in out
    assert "primary key (" not in out.lower()


def test_translate_create_table_omits_inline_references_for_bulk_load() -> None:
    ddl = "CREATE TABLE foreign_data (uuid TEXT references cards (uuid), language TEXT)"
    out = translate_create_table(ddl)
    assert "uuid TEXT" in out
    assert "language TEXT" in out
    assert "references" not in out.lower()


def test_translate_create_table_omits_multiline_inline_references_for_bulk_load() -> None:
    ddl = """
    CREATE TABLE legalities (
        id INTEGER not null primary key autoincrement,
        uuid TEXT
            references cards (uuid)
                on update cascade on delete cascade
    )
    """
    out = translate_create_table(ddl)
    assert "uuid TEXT" in out
    assert "references" not in out.lower()
    assert "cascade" not in out.lower()


def test_translate_create_table_ignores_commented_out_columns() -> None:
    ddl = """
    CREATE TABLE satscores (
        cds TEXT not null primary key,
        NumGE1500 INTEGER null,
    --  PctGE1500 double null,
        foreign key (cds) references schools (CDSCode)
    );
    """
    out = translate_create_table(ddl)
    assert "PctGE1500" not in out
    assert '"--"' not in out
    assert "numge1500 BIGINT null" in out
    assert "foreign key" not in out.lower()
    assert "references" not in out.lower()


def test_translate_create_table_removes_sqlite_autoincrement_keyword() -> None:
    ddl = "CREATE TABLE cards (id INTEGER not null primary key autoincrement, name TEXT)"
    out = translate_create_table(ddl)
    assert "autoincrement" not in out.lower()
    assert "id BIGINT not null primary key" in out
    assert "name TEXT" in out


def test_translate_create_table_removes_invalid_zero_date_default() -> None:
    ddl = "CREATE TABLE races (date DATE default '0000-00-00' not null, name TEXT)"
    out = translate_create_table(ddl)
    assert "'0000-00-00'" not in out
    assert "date DATE not null" in out
    assert "name TEXT" in out
