from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import pymysql
from pymysql.cursors import DictCursor

from .config import settings


_DEFAULT_DATABASE = object()


def connect(database: Optional[str] | object = _DEFAULT_DATABASE):
    db_name = settings.db_name if database is _DEFAULT_DATABASE else database
    kwargs = dict(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
    )
    if db_name:
        kwargs["database"] = db_name
    return pymysql.connect(**kwargs)


def run_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    statements = split_sql(sql)
    with connect(database=None) as conn:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        conn.commit()


def split_sql(sql: str) -> Iterable[str]:
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";")
            buffer.clear()
            if statement:
                yield statement
    tail = "\n".join(buffer).strip()
    if tail:
        yield tail


def execute(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            rowcount = cur.execute(sql, params or {})
        conn.commit()
        return rowcount


def insert_and_get_id(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            lastrowid = int(cur.lastrowid or 0)
        conn.commit()
        return lastrowid


def fetch_one(sql: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchone()


def fetch_all(sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return list(cur.fetchall())
