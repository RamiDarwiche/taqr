from __future__ import annotations

from langchain.tools import tool
from sqlalchemy import inspect, text

from db import DB

_db = DB()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _list_table_names() -> list[str]:
    return inspect(_db.get_engine()).get_table_names()


def _format_table_schema(table: str) -> str:
    inspector = inspect(_db.get_engine())
    columns = inspector.get_columns(table)
    pk = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
    col_defs = []
    for col in columns:
        col_type = str(col["type"])
        parts = [f"{_quote_ident(col['name'])} {col_type}"]
        if col["name"] in pk:
            parts.append("PRIMARY KEY")
        if not col.get("nullable", True):
            parts.append("NOT NULL")
        col_defs.append(" ".join(parts))
    return f"CREATE TABLE {_quote_ident(table)} (\n  " + ",\n  ".join(col_defs) + "\n)"


@tool
def sql_db_list_tables() -> str:
    """Input is an empty string, output is a comma-separated list of tables in the database."""
    return ", ".join(_list_table_names())


@tool
def sql_db_schema(table_names: str) -> str:
    """Input to this tool is a comma-separated list of tables, output is the schema and sample rows for those tables.
    Be sure that the tables actually exist by calling sql_db_list_tables first!
    Example Input: table1, table2, table3"""
    valid_tables = set(_list_table_names())
    results: list[str] = []

    for table in table_names.split(","):
        table = table.strip()
        if table not in valid_tables:
            results.append(f"Error: table_names {{{table!r}}} not found in database")
            continue

        results.append(_format_table_schema(table))
        try:
            with _db.get_engine().connect() as conn:
                rows = conn.execute(
                    text(f"SELECT * FROM {_quote_ident(table)} LIMIT 3")
                ).fetchall()
                if rows:
                    col_names = list(rows[0]._mapping.keys())
                    results.append(
                        f"/*\n3 rows from {table} table:\n"
                        + "\t".join(col_names)
                        + "\n"
                        + "\n".join("\t".join(str(x) for x in row) for row in rows)
                        + "\n*/"
                    )
        except Exception as e:
            results.append(f"Error fetching sample rows: {e}")

    return "\n\n".join(results)


@tool
def sql_db_query(query: str) -> str:
    """Input to this tool is a detailed and correct SQL query, output is a result from the database.
    If the query is not correct, an error message will be returned.
    If an error is returned, rewrite the query, check the query, and try again.
    If you encounter an issue with Unknown column 'xxxx' in 'field list', use sql_db_schema to query the correct table fields.
    """
    try:
        with _db.get_engine().connect() as conn:
            result = conn.execute(text(query))
            return str(result.fetchall())
    except Exception as e:
        return f"Error: {e}"


@tool
def sql_db_query_checker(query: str) -> str:
    """Use this tool to double check if your query is correct before executing it.
    Always use this tool before executing a query with sql_db_query!"""
    from planner.llm import model

    trigger_prompt = f"""{query}
Double check the PostgreSQL query above for common mistakes, including:
- Using NOT IN with NULL values
- Using UNION when UNION ALL should have been used
- Using BETWEEN for exclusive ranges
- Data type mismatch in predicates
- Properly quoting identifiers
- Using the correct number of arguments for functions
- Casting to the correct data type
- Using the proper columns for joins
- SQLite-specific syntax that should be Postgres (e.g. AUTOINCREMENT, sqlite_master)

If there are any of the above mistakes, rewrite the query. If there are no mistakes, just reproduce the original query.

Output the final SQL query only.

SQL Query: """

    response = model.invoke(trigger_prompt)
    return response.text.strip()
