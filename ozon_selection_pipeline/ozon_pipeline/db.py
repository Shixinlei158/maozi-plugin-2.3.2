from __future__ import annotations

import re
import time as time_mod
from pathlib import Path
from typing import Any, Iterable, Optional
from threading import Lock

import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import OperationalError

from .config import settings

_DEFAULT_DATABASE = object()
_IGNORABLE_MIGRATION_ERROR_CODES = {1060, 1061}

_TRANSIENT_ERROR_CODES = {0, 1205, 2003, 2006, 2013}
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 0.5

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
            
        # Defensive configuration for Tailscale/WireGuard VPN:
        # - pool_recycle=600: recycle before WireGuard rekey (every ~2min) creates stale connections
        # - pool_pre_ping=True: verify connection liveness before each use
        # - lock_wait_timeout=10: if a prior dead connection holds row locks, fail fast instead of
        #   waiting 50s (MySQL default) and cascading timeouts across all workers
        engine = create_engine(
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
                "init_command": "SET SESSION lock_wait_timeout=10, SESSION innodb_lock_wait_timeout=10",
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


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        orig = getattr(exc, "orig", None)
        if orig is not None:
            code = getattr(orig, "args", [None])[0] if hasattr(orig, "args") else None
            if code in _TRANSIENT_ERROR_CODES:
                return True
        return True
    return False


def _retry_on_transient(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if _is_transient_error(exc) and attempt < _MAX_RETRIES:
                time_mod.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise
    raise last_exc


def execute(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    def _do():
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(_prepare_sql(sql)), params or {})
            return result.rowcount
    return _retry_on_transient(_do)


def execute_many(sql: str, params: Iterable[dict[str, Any]]) -> int:
    """逐条执行多条参数（使用 SQLAlchemy text()，适用于非 INSERT 或少量数据）"""
    def _do():
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(_prepare_sql(sql)), list(params))
            return result.rowcount
    return _retry_on_transient(_do)


def execute_insert_many(sql: str, params: list[dict[str, Any]], *, batch_size: int = 0) -> int:
    """批量 INSERT：将模板 SQL 转为单条多 VALUES 语句执行。

    sql 使用 PyMySQL %(name)s 占位符，如:
      INSERT INTO t (a, b) VALUES (%(a)s, CURRENT_TIMESTAMP) ON DUPLICATE KEY UPDATE ...
    自动为每行生成唯一参数名，合成一条多 VALUES 的 INSERT 执行。
    VALUES 中可混用 %(name)s 参数和字面值（CURRENT_TIMESTAMP / NULL 等）。
    """
    param_list = list(params)
    if not param_list:
        return 0

    import re as _re

    # 拆分 SQL：抓出 VALUES (...) 中的内容以及前缀/后缀
    m = _re.match(
        r'(.*?VALUES)\s*\(((?:[^()]|%\([^)]*\)[^()]?)*)\)\s*(ON\s+DUPLICATE\s+KEY\s+UPDATE.*)',
        sql, _re.DOTALL | _re.IGNORECASE
    )
    if not m:
        m = _re.match(
            r'(.*?VALUES)\s*\(((?:[^()]|%\([^)]*\)[^()]?)*)\)\s*$',
            sql, _re.DOTALL | _re.IGNORECASE
        )
    if not m:
        raise ValueError(f"execute_insert_many: cannot parse INSERT template: {sql[:200]}")

    prefix = m.group(1)
    row_body = m.group(2).strip()
    suffix = m.group(3).strip() if m.lastindex and m.lastindex >= 3 else ""

    def _do_one(batch: list[dict[str, Any]]) -> int:
        if not batch:
            return 0
        parts = []
        flat = {}
        for i, row in enumerate(batch):
            # 替换每个 %(name)s 为 %(rN_name)s，保留字面值
            def _replace(mo):
                name = mo.group(1)
                pn = f"r{i}_{name}"
                flat[pn] = row.get(name)
                return f"%({pn})s"
            replaced = _re.sub(r'%\((\w+)\)s', _replace, row_body)
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
            chunk = param_list[i : i + batch_size]
            total += _retry_on_transient(_do_one, chunk)
        return total
    return _retry_on_transient(_do_one, param_list)


def insert_and_get_id(sql: str, params: Optional[dict[str, Any]] = None) -> int:
    def _do():
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(_prepare_sql(sql)), params or {})
            return result.lastrowid
    return _retry_on_transient(_do)


def fetch_one(sql: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    def _do():
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().first()
            return dict(result) if result else None
    return _retry_on_transient(_do)


def fetch_all(sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[dict, Any]]:
    def _do():
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(_prepare_sql(sql)), params or {}).mappings().all()
            return [dict(r) for r in result]
    return _retry_on_transient(_do)

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
