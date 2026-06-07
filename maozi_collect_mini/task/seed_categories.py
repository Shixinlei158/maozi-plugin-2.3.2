"""类目树种子数据导入工具。

从 data/category_tree.json 将Ozon三级类目树导入 ozon_categories 表。
用法: python -m task.seed_categories
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..config import ROOT_DIR, CATEGORY_TREE_FILE
from ..repository import upsert_category

SQL_DIR = ROOT_DIR / "sql"


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_tree(data: dict[str, Any]) -> list[dict[str, Any]]:
    """将嵌套的 c1/c2/c3 对象展平为数据库行列表"""
    rows: list[dict[str, Any]] = []

    c1_map = data.get("c1", {})
    c2_map = data.get("c2", {})
    c3_map = data.get("c3", {})

    for cid, info in c1_map.items():
        rows.append({
            "category_id": int(info["id"]),
            "name_zh": info.get("zh", ""),
            "name_en": info.get("en", ""),
            "parent_id": 0,
            "level": 1,
        })

    for cid, info in c2_map.items():
        name_zh = info.get("zh", "")
        name_en = info.get("en", "")
        if not name_zh and name_en:
            name_zh = name_en
        rows.append({
            "category_id": int(info["id"]),
            "name_zh": name_zh,
            "name_en": name_en,
            "parent_id": int(info.get("parent", 0)),
            "level": 2,
        })

    for cid, info in c3_map.items():
        name_zh = info.get("zh", "")
        name_en = info.get("en", "")
        if not name_zh and name_en:
            name_zh = name_en
        rows.append({
            "category_id": int(info["id"]),
            "name_zh": name_zh,
            "name_en": name_en,
            "parent_id": int(info.get("parent", 0)),
            "level": 3,
        })

    return rows


def seed(rows: list[dict[str, Any]], batch_size: int = 200) -> None:
    """批量插入类目数据"""
    if not rows:
        print("[SEED] 没有数据需要导入")
        return

    sql = (
        "INSERT INTO ozon_categories (category_id, name_zh, name_en, parent_id, level) "
        "VALUES (%(category_id)s, %(name_zh)s, %(name_en)s, %(parent_id)s, %(level)s) "
        "ON DUPLICATE KEY UPDATE "
        "name_zh=VALUES(name_zh), name_en=VALUES(name_en), "
        "parent_id=VALUES(parent_id), level=VALUES(level)"
    )

    total = db.execute_insert_many(sql, rows, batch_size=batch_size)
    print(f"[SEED] 导入完成: {total} 条记录")


def run() -> None:
    init_sql = SQL_DIR / "init.sql"
    if init_sql.exists():
        db.run_sql_file(init_sql)
        print(f"[MIGRATE] 执行建表: {init_sql.name}")
    else:
        print("[WARN] init.sql 不存在，跳过建表")

    if not CATEGORY_TREE_FILE.exists():
        print(f"[WARN] JSON 文件不存在: {CATEGORY_TREE_FILE}")
        return

    print(f"[INFO] 加载: {CATEGORY_TREE_FILE}")
    data = load_json(CATEGORY_TREE_FILE)
    c1 = data.get("c1Count", 0)
    c2 = data.get("c2Count", 0)
    c3 = data.get("c3Count", 0)
    print(f"[INFO] 一级 {c1} 二级 {c2} 三级 {c3}")

    rows = flatten_tree(data)
    print(f"[INFO] 共 {len(rows)} 条类目记录")
    seed(rows)
    print("[DONE] 类目树导入完成")


if __name__ == "__main__":
    run()
