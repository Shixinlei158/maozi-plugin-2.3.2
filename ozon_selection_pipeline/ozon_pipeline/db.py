from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional
from threading import Lock

import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from .config import settings

_DEFAULT_DATABASE = object()
_IGNORABLE_MIGRATION_ERROR_CODES = {1060, 1061}

_engine_cache: dict[str, Any] = {}
_engine_lock = Lock()

def get_engine(database: Optional[str] = None):
    db_name = database if database is not None else settings.db_name
    cache_key = db_name or ""
    
    with _engine_lock:
        if cache_key in _engine_cache:
            return _engine_cache[cache_key]
        
        # Build connection URL
        url = f"mysql+pymysql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}"
        if db_name:
            url += f"/{db_name}"
            
        # Defensive configuration as per rules
        engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": settings.db_connect_timeout,
                "read_timeout": settings.db_read_timeout,
                "write_timeout": settings.db_write_timeout,
                "charset": "utf8mb4",
            }
        )
        _engine_cache[cache_key] = engine
        return engine

def connect(database: Optional[str] | object = _DEFAULT_DATABASE):
    db_name = settings.db_name if database is _DEFAULT_DATABASE else database
    engine = get_engine(db_name if isinstance(db_name, str) else None)
    return engine.raw_connection()

def _prepare_sql(sql: str) -> str:
    # Convert PyMySQL style %(name)s to SQLAlchemy style :name
    # Also handle escaped %%
    sql = sql.replace("%%", "%")
    return re.sub(r'%\((\w+)\)s', r':\1', sql)

def execute(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {})
        return result.rowcount

def execute_many(sql: str, params: Iterable[dict[str, Any]]) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text(_prepare_sql(sql)), list(params))
        return result.rowcount

def insert_and_get_id(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {})
        return result.lastrowid

def fetch_one(sql: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().first()
        return dict(result) if result else None

def fetch_all(sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[dict, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().all()
        return [dict(r) for r in result]

def run_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    statements = split_sql(sql)
    engine = get_engine(database=None)
    with engine.begin() as conn:
        for statement in statements:
            try:
                conn.execute(text(statement))
            except Exception as exc:
                msg = str(exc).upper()
                if "ALTER TABLE" in statement.upper() and any(f"({code})" in msg for code in _IGNORABLE_MIGRATION_ERROR_CODES):
                    continue
                raise

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
