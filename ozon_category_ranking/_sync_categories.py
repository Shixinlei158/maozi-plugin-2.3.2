"""Ozon 类目同步工具：对比官网类目与数据库，决定新建表或补充旧表。

用法：
    cd c:\project\maozi-plugin-2.3.2
    python -m ozon_category_ranking._sync_categories
"""

from __future__ import annotations

import json
import time
import sys
import os
from typing import Any

# 确保项目根目录在 path 中
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CDP_URL = "127.0.0.1:9222"
DPRINT = print


def dprint(*args):
    DPRINT(*args)


# ============================================================
# Part 1: 从 Ozon 获取全量类目树
# ============================================================
def fetch_ozon_category_tree() -> list[dict[str, Any]]:
    """CDP 连接浏览器，获取完整的一级→二级→三级类目树"""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright()
    pl = pw.start()
    browser = pl.chromium.connect_over_cdp(f"http://{CDP_URL}")

    all_categories: list[dict[str, Any]] = []

    try:
        ctx = browser.contexts[0]
        for p in ctx.pages:
            try:
                if "ozon.ru" in p.url:
                    page = p
                    break
            except Exception:
                continue
        else:
            page = ctx.new_page()
            page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

        # ---- 辅助函数 ----
        def entrypoint(path: str):
            r = page.evaluate(f"""
                async () => {{
                    const r = await fetch(`/api/entrypoint-api.bx/page/json/v2?url=${{encodeURIComponent('{path}')}}`, {{credentials:'include'}});
                    try {{ return JSON.parse(await r.text()); }} catch(e) {{ return null; }}
                }}
            """)
            return r

        def composer_action(action_name, params):
            body = json.dumps(params, ensure_ascii=False)
            r = page.evaluate(f"""
                async () => {{
                    const r = await fetch('/api/composer-api.bx/_action/v2/{action_name}', {{
                        method: 'POST', credentials: 'include',
                        headers: {{ 'Content-Type': 'application/json', 'x-o3-app-name': 'dweb_client' }},
                        body: JSON.stringify({body})
                    }});
                    try {{ return JSON.parse(await r.text()); }} catch(e) {{ return null; }}
                }}
            """)
            return r

        def extract_category_id(url: str) -> int | None:
            if not url or "-" not in url:
                return None
            parts = url.strip("/").split("/")[-1]
            if "-" in parts:
                tail = parts.rsplit("-", 1)[-1]
                return int(tail) if tail.isdigit() else None

        # ---- 步骤1: 获取一级类目 ----
        dprint("[1/4] 获取一级类目...")
        data = entrypoint("/")
        if not data:
            raise RuntimeError("无法获取主页 entrypoint 数据")

        ws = data.get("widgetStates", {})
        catalog_key = next((k for k in ws if k.startswith("catalogMenu-")), None)
        if not catalog_key:
            raise RuntimeError("未找到 catalogMenu widget")

        catalog_raw = ws[catalog_key]
        if isinstance(catalog_raw, str):
            catalog_raw = json.loads(catalog_raw)

        menu_id = catalog_raw.get("menuId", "")
        dprint(f"  catalogMenu menuId={menu_id}")

        level1_cats = catalog_raw.get("categories", [])
        dprint(f"  一级类目: {len(level1_cats)} 个")

        for cat in level1_cats:
            cid = int(cat["id"])
            url = cat.get("url", "")
            all_categories.append({
                "category_id": cid,
                "parent_id": 0,
                "level": 1,
                "name_ru": (cat.get("title", "") or "").strip(),
                "slug": url.strip("/").split("/")[-1].rsplit("-", 1)[0] if "-" in url else "",
                "url": url,
                "icon": cat.get("icon", ""),
                "image": cat.get("image", ""),
            })

        # ---- 步骤2: 获取二级类目 ----
        dprint(f"\n[2/4] 获取二级类目...")
        level2_count = 0
        for i, l1 in enumerate(level1_cats):
            base_url = l1.get("url", "")
            if not base_url:
                continue
            dprint(f"  ({i+1}/{len(level1_cats)}) {l1.get('title','')[:30]}...")
            try:
                result = composer_action("getCatalogFilterValues", {
                    "baseLink": base_url,
                    "isOpened": "true",
                    "key": "category",
                    "pageType": "category",
                })
                if not result:
                    continue
                data_obj = result.get("data", result)
                sub_cats = data_obj.get("categories", [])
                for sc in sub_cats:
                    if sc.get("isActive"):
                        continue  # 跳过当前类目自身
                    lvl = sc.get("level", 0)
                    url = sc.get("urlValue", "")
                    cid = extract_category_id(url)
                    if not cid or lvl != 1:
                        continue
                    p_id = l1.get("id")
                    if p_id:
                        p_id = int(p_id)
                    else:
                        p_id = 0
                    all_categories.append({
                        "category_id": cid,
                        "parent_id": p_id,
                        "level": 2,
                        "name_ru": (sc.get("title", "") or "").strip(),
                        "slug": url.strip("/").split("/")[-1].rsplit("-", 1)[0] if "-" in url else "",
                        "url": url,
                        "icon": "",
                        "image": "",
                    })
                    level2_count += 1
                time.sleep(0.3)
            except Exception as e:
                dprint(f"    [WARN] {e}")
        dprint(f"  二级类目: {level2_count} 个")

        # ---- 步骤3+4+: 递归获取更深层类目（三级、四级、五级...直到末级） ----
        # 策略：从当前最深层级（level=2）开始，逐层向下探索
        # 每轮处理当前层级的所有类目，收集子类目作为下一层
        # 当某层没有任何子类目时停止
        current_level = 2  # 当前检查的层级（已有数据中最大level=2）
        total_deeper = 0

        while True:
            dprint(f"\n[递归] 从 level={current_level} 获取 level={current_level+1} 子类目...")
            parent_list = [c for c in all_categories if c["level"] == current_level]
            if not parent_list:
                dprint(f"  level={current_level} 没有类目，停止")
                break

            child_count = 0
            for i, parent in enumerate(parent_list):
                url = parent.get("url", "")
                if not url:
                    continue
                if (i + 1) % 50 == 0:
                    dprint(f"  ({i+1}/{len(parent_list)}) 已发现{child_count}个level={current_level+1}子类目...")
                try:
                    result = composer_action("getCatalogFilterValues", {
                        "baseLink": url,
                        "isOpened": "true",
                        "key": "category",
                        "pageType": "category",
                    })
                    if not result:
                        continue
                    data_obj = result.get("data", result)
                    sub_cats = data_obj.get("categories", [])
                    for sc in sub_cats:
                        if sc.get("isActive"):
                            continue
                        lvl = sc.get("level", 0)
                        sub_url = sc.get("urlValue", "")
                        cid = extract_category_id(sub_url)
                        if not cid or lvl != 1:
                            continue
                        # 去重：检查是否已存在（可能被其他父类目重复引用）
                        existing = [c for c in all_categories if c["category_id"] == cid]
                        if existing:
                            # 如果已存在但parent_id不同，记录在日志中（暂不处理多父情况）
                            continue
                        all_categories.append({
                            "category_id": cid,
                            "parent_id": parent["category_id"],
                            "level": current_level + 1,
                            "name_ru": (sc.get("title", "") or "").strip(),
                            "slug": sub_url.strip("/").split("/")[-1].rsplit("-", 1)[0] if "-" in sub_url else "",
                            "url": sub_url,
                            "icon": "",
                            "image": "",
                        })
                        child_count += 1
                    time.sleep(0.1)
                except Exception as e:
                    pass  # 超时等静默跳过

            if child_count == 0:
                dprint(f"  level={current_level} 没有任何子类目，已到达最深层")
                break

            total_deeper += child_count
            dprint(f"  level={current_level+1} 类目: {child_count} 个")
            current_level += 1

            # 最大深度限制
            MAX_DEPTH = 4
            if current_level >= MAX_DEPTH:
                dprint(f"  [INFO] 已达到最大深度 level={MAX_DEPTH}，停止探索")
                break

        # 统计各层数量
        level_counts = {}
        for c in all_categories:
            lv = c["level"]
            level_counts[lv] = level_counts.get(lv, 0) + 1
        stats_str = " + ".join(f"{lv}级:{cnt}" for lv, cnt in sorted(level_counts.items()))
        dprint(f"\n[完成] 从Ozon官网获取全量类目: {stats_str} = 共{len(all_categories)}个类目")
        return all_categories

    finally:
        pl.stop()


# ============================================================
# Part 2: 查询数据库 ozon_categories
# ============================================================
def fetch_db_categories() -> list[dict[str, Any]]:
    """从数据库查询所有类目"""
    try:
        from maozi_collect_mini import db
        from maozi_collect_mini.db import fetch_all
        rows = fetch_all(
            "SELECT category_id, name_zh, name_en, parent_id, level FROM ozon_categories ORDER BY level, category_id"
        )
        dprint(f"\n数据库 ozon_categories: {len(rows)} 条记录")
        for lvl in [1, 2, 3]:
            count = sum(1 for r in rows if r["level"] == lvl)
            dprint(f"  级别{lvl}: {count} 条")
        return rows
    except Exception as e:
        dprint(f"[ERROR] 数据库查询失败: {e}")
        return []


# ============================================================
# Part 3: 对比
# ============================================================
def compare(ozon_cats: list[dict], db_cats: list[dict]):
    """对比两组数据"""
    dprint("\n" + "=" * 70)
    dprint("对比结果")
    dprint("=" * 70)

    ozon_ids: dict[int, dict] = {c["category_id"]: c for c in ozon_cats}
    db_ids: dict[int, dict] = {c["category_id"]: c for c in db_cats}

    # 统计
    ozon_l1 = {c["category_id"] for c in ozon_cats if c["level"] == 1}
    db_l1 = {c["category_id"] for c in db_cats if c["level"] == 1}

    dprint(f"\nOzon一级类目: {len(ozon_l1)} 个")
    dprint(f"数据库一级类目: {len(db_l1)} 个")

    # 一级类目 ID 交集
    common_l1 = ozon_l1 & db_l1
    only_ozon_l1 = ozon_l1 - db_l1
    only_db_l1 = db_l1 - ozon_l1

    dprint(f"\n一级类目 ID 交集: {len(common_l1)} 个")
    if only_ozon_l1:
        dprint(f"仅Ozon有的一级(数据库缺失): {sorted(only_ozon_l1)}")
    if only_db_l1:
        dprint(f"仅数据库有的一级(Ozon没有): {sorted(only_db_l1)}")

    # 判断是否一致
    if len(common_l1) == 0:
        is_match = False
        dprint("\n>>> 结论: 类目ID完全不一致！需要新建表")
    elif len(common_l1) == len(ozon_l1) and len(common_l1) == len(db_l1):
        is_match = True
        dprint("\n>>> 结论: 一级类目ID完全一致")
    elif len(common_l1) >= len(ozon_l1) * 0.5:
        is_match = True
        dprint(f"\n>>> 结论: 一级类目ID大部分一致(交集{len(common_l1)}/{len(ozon_l1)})，视为一致")
    else:
        is_match = False
        dprint(f"\n>>> 结论: 一级类目ID大部分不一致(交集{len(common_l1)}/{len(ozon_l1)})，需要新建表")

    # 二级三级也对比
    for lvl in [2, 3]:
        ozon_lv = {c["category_id"] for c in ozon_cats if c["level"] == lvl}
        db_lv = {c["category_id"] for c in db_cats if c["level"] == lvl}
        common_lv = ozon_lv & db_lv
        only_o = ozon_lv - db_lv
        dprint(f"\n{lvl}级: Ozon={len(ozon_lv)}, DB={len(db_lv)}, 交集={len(common_lv)}, Ozon独有={len(only_o)}")

    # 数据库完整性
    missing = set(ozon_ids.keys()) - set(db_ids.keys())
    extra = set(db_ids.keys()) - set(ozon_ids.keys())
    dprint(f"\n数据库缺失的类目: {len(missing)} 个")
    dprint(f"数据库中多余的类目: {len(extra)} 个")

    return is_match, missing, extra


# ============================================================
# Part 4: 写入数据库
# ============================================================
def create_ozon_category_urls_table():
    """创建新表 ozon_category_urls"""
    from maozi_collect_mini import db
    from sqlalchemy import text as sa_text

    dprint("\n创建新表 ozon_category_urls...")
    sql = (
        "CREATE TABLE IF NOT EXISTS ozon_category_urls ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "category_id BIGINT NOT NULL COMMENT 'Ozon类目ID', "
        "parent_id BIGINT NOT NULL DEFAULT 0 COMMENT '父级类目ID', "
        "level TINYINT NOT NULL COMMENT '1=一级 2=二级 3=三级 4=四级 ...', "
        "name_ru VARCHAR(255) NOT NULL DEFAULT '' COMMENT '类目俄语名', "
        "slug VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'URL slug', "
        "url VARCHAR(512) NOT NULL DEFAULT '' COMMENT '相对URL', "
        "icon VARCHAR(512) NOT NULL DEFAULT '' COMMENT '类目图标URL', "
        "image VARCHAR(512) NOT NULL DEFAULT '' COMMENT '类目图片URL', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_category_id (category_id), "
        "INDEX idx_parent_id (parent_id), "
        "INDEX idx_level (level)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci "
        "COMMENT='Ozon类目URL映射表'"
    )
    engine = db.get_engine()
    with engine.begin() as conn:
        conn.execute(sa_text(sql))
    dprint("  表 ozon_category_urls 已就绪")


def import_to_new_table(ozon_cats: list[dict]):
    """导入全量数据到新表 ozon_category_urls"""
    from maozi_collect_mini import db
    from maozi_collect_mini.db import execute_insert_many

    rows = [
        {
            "category_id": c["category_id"],
            "parent_id": c["parent_id"],
            "level": c["level"],
            "name_ru": c.get("name_ru", ""),
            "slug": c.get("slug", ""),
            "url": c.get("url", ""),
            "icon": c.get("icon", ""),
            "image": c.get("image", ""),
        }
        for c in ozon_cats
    ]

    sql = (
        "INSERT INTO ozon_category_urls (category_id, parent_id, level, name_ru, slug, url, icon, image) "
        "VALUES (%(category_id)s, %(parent_id)s, %(level)s, %(name_ru)s, %(slug)s, %(url)s, %(icon)s, %(image)s) "
        "ON DUPLICATE KEY UPDATE "
        "name_ru=VALUES(name_ru), slug=VALUES(slug), url=VALUES(url), "
        "icon=VALUES(icon), image=VALUES(image), parent_id=VALUES(parent_id)"
    )

    total = execute_insert_many(sql, rows, batch_size=200)
    dprint(f"\n导入完成: {total} 条记录写入 ozon_category_urls")


def alter_old_table_add_url_column():
    """在旧表 ozon_categories 中补充 url 字段"""
    from maozi_collect_mini import db
    from sqlalchemy import text as sa_text

    dprint("\n检查旧表 ozon_categories 是否有 url 字段...")
    engine = db.get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(sa_text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'ozon_categories' AND column_name = 'url'"
            ))
            has_url = result.fetchone() is not None
    except Exception:
        has_url = False

    if not has_url:
        dprint("  旧表无 url 字段，添加中...")
        with engine.begin() as conn:
            conn.execute(sa_text(
                "ALTER TABLE ozon_categories ADD COLUMN url VARCHAR(512) NOT NULL DEFAULT '' "
                "COMMENT '类目导航URL' AFTER name_en"
            ))
        dprint("  已添加 url 字段")
    else:
        dprint("  url 字段已存在")

    # 检查是否有 slug 字段
    try:
        with engine.begin() as conn:
            result = conn.execute(sa_text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'ozon_categories' AND column_name = 'slug'"
            ))
            has_slug = result.fetchone() is not None
    except Exception:
        has_slug = False
    if not has_slug:
        dprint("  旧表无 slug 字段，添加中...")
        with engine.begin() as conn:
            conn.execute(sa_text(
                "ALTER TABLE ozon_categories ADD COLUMN slug VARCHAR(255) NOT NULL DEFAULT '' "
                "COMMENT 'URL slug' AFTER url"
            ))
        dprint("  已添加 slug 字段")
    else:
        dprint("  slug 字段已存在")


def update_old_table_urls(ozon_cats: list[dict]):
    """用 Ozon 数据补充旧表的 url 和 slug 字段"""
    from maozi_collect_mini import db
    from maozi_collect_mini.db import execute_insert_many

    rows = [
        {
            "category_id": c["category_id"],
            "url": c.get("url", ""),
            "slug": c.get("slug", ""),
        }
        for c in ozon_cats
    ]
    sql = (
        "INSERT INTO ozon_categories (category_id, url, slug) "
        "VALUES (%(category_id)s, %(url)s, %(slug)s) "
        "ON DUPLICATE KEY UPDATE url=VALUES(url), slug=VALUES(slug)"
    )
    updated = execute_insert_many(sql, rows, batch_size=200)
    dprint(f"\n更新旧表 url/slug: {updated} 条")


def insert_missing_categories(ozon_cats: list[dict], missing_ids: set[int]):
    """补全旧表中缺失的类目"""
    from maozi_collect_mini import db
    from maozi_collect_mini.db import execute_insert_many

    missing_rows = [
        {
            "category_id": c["category_id"],
            "name_zh": "",
            "name_en": "",
            "parent_id": c["parent_id"],
            "level": c["level"],
            "url": c.get("url", ""),
            "slug": c.get("slug", ""),
        }
        for c in ozon_cats
        if c["category_id"] in missing_ids
    ]
    if missing_rows:
        sql = (
            "INSERT INTO ozon_categories (category_id, name_zh, name_en, parent_id, level, url, slug) "
            "VALUES (%(category_id)s, %(name_zh)s, %(name_en)s, %(parent_id)s, %(level)s, %(url)s, %(slug)s) "
            "ON DUPLICATE KEY UPDATE url=VALUES(url), slug=VALUES(slug)"
        )
        n = execute_insert_many(sql, missing_rows, batch_size=200)
        dprint(f"\n补全缺失类目: {n} 条")
    else:
        dprint("\n无需补全，旧表数据已完整")


# ============================================================
# Main
# ============================================================
def main():
    dprint("=" * 70)
    dprint("Ozon 类目同步工具")
    dprint("=" * 70)

    # 1. 获取 Ozon 全量类目
    dprint("\n>>> 从Ozon官网获取全量类目...")
    ozon_cats = fetch_ozon_category_tree()
    dprint(f"获取到 {len(ozon_cats)} 个Ozon类目")

    # 保存原始数据
    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, "_ozon_categories_raw.json"), "w", encoding="utf-8") as f:
        json.dump(ozon_cats, f, ensure_ascii=False, indent=2)
    dprint(f"原始数据已保存: _ozon_categories_raw.json")

    # 2. 查询数据库
    dprint("\n>>> 查询数据库 ozon_categories...")
    db_cats = fetch_db_categories()

    if not db_cats:
        dprint("[FATAL] 无法连接数据库，跳过对比")
        return

    # 3. 对比
    is_match, missing_ids, extra_ids = compare(ozon_cats, db_cats)

    # 4. 执行操作
    dprint("\n" + "=" * 70)
    dprint("执行结果")
    dprint("=" * 70)

    if is_match:
        dprint("\n>>> 方案: 类目ID一致，补充旧表")
        alter_old_table_add_url_column()
        update_old_table_urls(ozon_cats)

        if missing_ids:
            dprint(f"\n旧表缺失 {len(missing_ids)} 个类目，补全中...")
            insert_missing_categories(ozon_cats, missing_ids)
        else:
            dprint("\n旧表数据已完整，无需补全")
    else:
        dprint("\n>>> 方案: 类目ID不一致，新建 ozon_category_urls 表")
        create_ozon_category_urls_table()
        import_to_new_table(ozon_cats)

    dprint("\n[DONE] 类目同步完成")


if __name__ == "__main__":
    main()
