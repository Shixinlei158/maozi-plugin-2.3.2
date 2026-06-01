"""
Ozon 类目树种子数据导入脚本

用法:
    python -m ozon_pipeline.seed_categories [--json category_tree.json]

功能:
    1. 从 JSON 文件读取三级类目树数据
    2. 执行迁移 SQL 建表
    3. 批量插入类目数据（支持重复执行，ON DUPLICATE KEY UPDATE 更新名称）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ozon_pipeline.db import execute, execute_insert_many, run_sql_file
from ozon_pipeline.config import ROOT_DIR

SQL_DIR = ROOT_DIR / "sql"
SEED_COLUMNS = ("category_id", "name_zh", "name_en", "parent_id", "level")


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_tree(data: dict[str, Any]) -> list[dict[str, Any]]:
    """将嵌套的 c1/c2/c3 对象展平为数据库行列表"""
    rows: list[dict[str, Any]] = []

    c1_map: dict[int, dict[str, Any]] = data.get("c1", {})
    c2_map: dict[int, dict[str, Any]] = data.get("c2", {})
    c3_map: dict[int, dict[str, Any]] = data.get("c3", {})

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
        # 如果中文名为空，尝试用英文名
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


def run_migration():
    """执行建表迁移"""
    migration_file = SQL_DIR / "010_ozon_categories.sql"
    if not migration_file.exists():
        print(f"[SKIP] 迁移文件不存在: {migration_file}")
        return
    print(f"[MIGRATE] 执行: {migration_file.name}")
    run_sql_file(migration_file)
    print("[MIGRATE] 完成")


def seed(rows: list[dict[str, Any]], batch_size: int = 200):
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

    total = execute_insert_many(sql, rows, batch_size=batch_size)
    print(f"[SEED] 导入完成: {total} 条记录")


def upsert_category(
    category_id: int,
    name_zh: str = "",
    name_en: str = "",
    parent_id: int = 0,
    level: int = 1,
) -> int:
    """自补充机制：单条插入/更新一个类目（供 repository 调用）"""
    sql = (
        "INSERT INTO ozon_categories (category_id, name_zh, name_en, parent_id, level) "
        "VALUES (%(category_id)s, %(name_zh)s, %(name_en)s, %(parent_id)s, %(level)s) "
        "ON DUPLICATE KEY UPDATE "
        "name_zh=IF(VALUES(name_zh) != '', VALUES(name_zh), name_zh), "
        "name_en=IF(VALUES(name_en) != '', VALUES(name_en), name_en), "
        "parent_id=IF(VALUES(parent_id) != 0, VALUES(parent_id), parent_id)"
    )
    return execute(sql, {
        "category_id": category_id,
        "name_zh": name_zh or "",
        "name_en": name_en or "",
        "parent_id": parent_id,
        "level": level,
    })


def ensure_categories_from_top_list(item: dict[str, Any]) -> int:
    """从 top-list 单条商品数据中提取并补充类目树。
    
    一次调用补齐 cate1/cate2/cate3 三级的类目记录。
    返回成功 upsert 的记录数。
    """
    cate1_id = item.get("cate1_id")
    cate2_id = item.get("cate2_id")
    cate3_id = item.get("cate3_id")
    cate1 = item.get("cate1") or ""
    cate2 = item.get("cate2") or ""
    cate3 = item.get("cate3") or ""
    category1 = item.get("category1") or ""
    category2 = item.get("category2") or ""
    category3 = item.get("category3") or ""

    count = 0
    if cate1_id:
        # level 1: cate1 的中文名来自 cate1, 英文名来自 category1
        upsert_category(
            category_id=int(cate1_id),
            name_zh=cate1,
            name_en=category1,
            parent_id=0,
            level=1,
        )
        count += 1

    if cate2_id:
        name_zh = cate2 or category2 or ""
        upsert_category(
            category_id=int(cate2_id),
            name_zh=name_zh,
            name_en=category2 or "",
            parent_id=int(cate1_id) if cate1_id else 0,
            level=2,
        )
        count += 1

    if cate3_id:
        name_zh = cate3 or category3 or ""
        upsert_category(
            category_id=int(cate3_id),
            name_zh=name_zh,
            name_en=category3 or "",
            parent_id=int(cate2_id) if cate2_id else 0,
            level=3,
        )
        count += 1

    return count


def main():
    # 先执行迁移
    run_migration()

    # 查找 JSON 文件
    json_path = ROOT_DIR / "category_tree.json"
    if len(sys.argv) > 1 and sys.argv[1].startswith("--json"):
        json_path = Path(sys.argv[2]) if len(sys.argv) > 2 else json_path

    if not json_path.exists():
        print(f"[WARN] JSON 文件不存在: {json_path}")
        print("[INFO] 请先运行提取脚本获取类目数据")
        return

    print(f"[INFO] 加载: {json_path}")
    data = load_json(json_path)
    print(f"[INFO] 一级 {data.get('c1Count', 0)} 二级 {data.get('c2Count', 0)} 三级 {data.get('c3Count', 0)}")

    rows = flatten_tree(data)
    print(f"[INFO] 共 {len(rows)} 条类目记录")
    seed(rows)

    print("[DONE] 类目树导入完成")


if __name__ == "__main__":
    main()
