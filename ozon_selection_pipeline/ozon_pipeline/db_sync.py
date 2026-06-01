"""数据库表结构同步：将本机SQL迁移文件应用到目标数据库"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db
from .config import ROOT_DIR, DBProfile


def sync_table_structure(target: DBProfile, dry_run: bool = False) -> dict[str, Any]:
    """将项目 sql/ 目录下的所有迁移 SQL 应用到目标数据库。

    Args:
        target: 目标数据库配置
        dry_run: True 时只检查不执行

    Returns:
        {"success": bool, "files": int, "statements": int, "errors": list[str]}
    """
    sql_dir = ROOT_DIR / "sql"
    if not sql_dir.exists():
        return {"success": False, "files": 0, "statements": 0, "errors": ["sql 目录不存在"]}

    # 收集所有 .sql 文件，按文件名排序
    sql_files = sorted(sql_dir.glob("*.sql"))
    if not sql_files:
        return {"success": False, "files": 0, "statements": 0, "errors": ["sql 目录下没有 .sql 文件"]}

    # 临时切换到目标数据库
    original_key = None
    for k, v in db.DB_PROFILES.items():
        if v.host == target.host and v.port == target.port and v.database == target.database:
            original_key = k
            break

    # 使用新连接执行迁移
    total_statements = 0
    errors: list[str] = []

    for sql_file in sql_files:
        content = sql_file.read_text(encoding="utf-8")
        if not content.strip():
            continue

        # 分割多条 SQL 语句
        statements = _split_sql(content)
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            total_statements += 1
            if dry_run:
                continue
            try:
                # 跳过 USE 语句（数据库名已由连接指定）
                if re.match(r'^\s*USE\s+', stmt, re.IGNORECASE):
                    continue
                # 跳过 CREATE DATABASE
                if re.match(r'^\s*CREATE\s+DATABASE\s+', stmt, re.IGNORECASE):
                    continue
                execute_raw_on(target, stmt)
            except Exception as e:
                err_str = str(e)
                # 忽略已存在的错误
                if any(code in err_str for code in ["1060", "1061", "1050", "Duplicate"]):
                    continue
                errors.append(f"{sql_file.name}: {err_str[:200]}")

    return {
        "success": len(errors) == 0,
        "files": len(sql_files),
        "statements": total_statements,
        "errors": errors,
    }


def execute_raw_on(profile: DBProfile, sql: str) -> None:
    """在指定数据库上执行原始SQL"""
    import pymysql
    conn = pymysql.connect(
        host=profile.host,
        port=profile.port,
        user=profile.user,
        password=profile.password,
        database=profile.database,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _split_sql(content: str) -> list[str]:
    """按分号分割SQL语句，处理字符串中的分号"""
    statements = []
    current = []
    in_string = False
    string_char = None
    for char in content:
        if char in ("'", '"') and not (current and current[-1] == '\\'):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None
        if char == ';' and not in_string:
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        statements.append("".join(current))
    return statements
