from __future__ import annotations

import re
import time as time_mod
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import QueuePool

from .config import settings

_TRANSIENT_ERROR_CODES = {0, 1205, 2003, 2006, 2013}
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 0.5

_engine = None
_engine_lock = Lock()


def get_engine():
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        url = (
            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )
        _engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_recycle=600,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": settings.db_connect_timeout,
                "read_timeout": settings.db_read_timeout,
                "write_timeout": settings.db_write_timeout,
                "charset": "utf8mb4",
            },
        )
        return _engine


def _prepare_sql(sql: str) -> str:
    """将 %(name)s 转换为 :name"""
    sql = sql.replace("%%", "%")
    return re.sub(r"%\((\w+)\)s", r":\1", sql)


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        orig = getattr(exc, "orig", None)
        if orig is not None:
            code = getattr(orig, "args", [None])[0] if hasattr(orig, "args") else None
            if code in _TRANSIENT_ERROR_CODES:
                return True
        return False
    return False


def execute(sql: str, params: dict[str, Any] | None = None) -> int:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            engine = get_engine()
            with engine.begin() as conn:
                result = conn.execute(text(_prepare_sql(sql)), params or {})
                return result.rowcount
        except Exception as exc:
            if _is_transient_error(exc) and attempt < _MAX_RETRIES:
                time_mod.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise


def execute_many(sql: str, params: Iterable[dict[str, Any]]) -> int:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            engine = get_engine()
            with engine.begin() as conn:
                result = conn.execute(text(_prepare_sql(sql)), list(params))
                return result.rowcount
        except Exception as exc:
            if _is_transient_error(exc) and attempt < _MAX_RETRIES:
                time_mod.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise


def execute_insert_many(sql: str, params: list[dict[str, Any]], *, batch_size: int = 0) -> int:
    """批量INSERT：将模板SQL转为多条VALUES"""
    import re as _re

    param_list = list(params)
    if not param_list:
        return 0

    m = _re.match(
        r"(.*?VALUES)\s*\(((?:[^()]|%\([^)]*\)[^()]?)*)\)\s*(ON\s+DUPLICATE\s+KEY\s+UPDATE.*)",
        sql, _re.DOTALL | _re.IGNORECASE,
    )
    if not m:
        m = _re.match(
            r"(.*?VALUES)\s*\(((?:[^()]|%\([^)]*\)[^()]?)*)\)\s*$",
            sql, _re.DOTALL | _re.IGNORECASE,
        )
    if not m:
        raise ValueError(f"Cannot parse INSERT template: {sql[:200]}")

    prefix = m.group(1)
    row_body = m.group(2).strip()
    suffix = m.group(3).strip() if m.lastindex and m.lastindex >= 3 else ""

    def _do_one(batch: list[dict[str, Any]]) -> int:
        parts = []
        flat = {}
        for i, row in enumerate(batch):
            def _replace(mo):
                name = mo.group(1)
                pn = f"r{i}_{name}"
                flat[pn] = row.get(name)
                return f"%({pn})s"
            replaced = _re.sub(r"%\((\w+)\)s", _replace, row_body)
            parts.append(f"({replaced})")

        full_sql = f"{prefix} {', '.join(parts)}"
        if suffix:
            full_sql += f" {suffix}"

        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(_prepare_sql(full_sql)), flat)
            return result.rowcount

    if batch_size and len(param_list) > batch_size:
        total = 0
        for i in range(0, len(param_list), batch_size):
            total += _do_one(param_list[i : i + batch_size])
        return total
    return _do_one(param_list)


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().first()
        return dict(result) if result else None


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().all()
        return [dict(r) for r in result]


def run_sql_file(path: Path) -> None:
    sql_text = path.read_text(encoding="utf-8")
    statements = _split_sql(sql_text)
    engine = get_engine()
    with engine.begin() as conn:
        for statement in statements:
            try:
                conn.execute(text(statement))
            except Exception as exc:
                msg = str(exc).upper()
                if "ALTER TABLE" in statement.upper() and any(
                    f"({code})" in msg for code in {1060, 1061}
                ):
                    continue
                raise


def _split_sql(sql: str) -> list[str]:
    buffer: list[str] = []
    statements: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buffer).strip().rstrip(";")
            buffer.clear()
            if stmt:
                statements.append(stmt)
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements
