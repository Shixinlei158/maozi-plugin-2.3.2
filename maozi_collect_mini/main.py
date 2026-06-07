"""主编排器。

功能：
- 启动时执行榜单采集
- 榜单采集完成后自动切换到卖家主页采集模式
- 支持无人值守循环模式（forever=true时持续运行）
"""

from __future__ import annotations

import sys
import time
import traceback

from .config import load_runtime_config
from .db import run_sql_file
from .config import ROOT_DIR


def ensure_tables() -> None:
    """确保数据库表存在"""
    init_sql = ROOT_DIR / "sql" / "init.sql"
    if init_sql.exists():
        try:
            run_sql_file(init_sql)
            print("[INIT] 数据库表结构已确认")
        except Exception as exc:
            print(f"[WARN] 建表失败: {exc}")


def run_pipeline() -> None:
    """运行采集流水线：榜单采集 → 卖家采集"""
    config = load_runtime_config()
    if not config:
        print("ERROR: config.json 不存在，请先在GUI中暂存配置")
        return

    ensure_tables()

    mode = config.get("_mode", "ranking")
    forever = config.get("forever", False)
    idle_seconds = int(config.get("idle_sleep_seconds", 300))

    from .ranking_collector import run_ranking_collection
    from .seller_collector import run_seller_collection

    while True:
        try:
            if mode in ("ranking", "crawl-top-list-network", "multi-category-network"):
                print("\n===== 阶段1: 榜单采集 =====")
                ranking_stats = run_ranking_collection(config)
                print(f"[榜单采集结果] {ranking_stats}")

            print("\n===== 阶段2: 卖家主页采集 =====")
            seller_stats = run_seller_collection(limit=0)
            print(f"[卖家采集结果] {seller_stats}")

        except Exception as exc:
            traceback.print_exc()
            print(f"[ERROR] 采集流水线异常: {exc}")

        if not forever:
            print("===== 采集完成 (非循环模式) =====")
            break

        print(f"\n-- 循环休眠 {idle_seconds} 秒后继续... --")
        time.sleep(idle_seconds)


if __name__ == "__main__":
    run_pipeline()
