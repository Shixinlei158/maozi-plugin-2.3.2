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


def _get_bool(raw: dict, key: str, default: bool = False) -> bool:
    val = raw.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"1", "true", "yes"}
    return bool(val)


def ensure_tables() -> None:
    """确保数据库表存在"""
    init_sql = ROOT_DIR / "sql" / "init.sql"
    if init_sql.exists():
        try:
            run_sql_file(init_sql)
            print("[INIT] 数据库表结构已确认")
        except Exception as exc:
            print(f"[WARN] 建表失败: {exc}")


def run_pipeline(stop_flag=None) -> None:
    """运行采集流水线：榜单采集/类目页采集 → 卖家采集

    参数:
        stop_flag: threading.Event 对象，用于外部停止采集
    """
    config = load_runtime_config()
    if not config:
        print("ERROR: config.json 不存在，请先在GUI中暂存配置")
        return

    ensure_tables()

    mode = config.get("_mode", "ranking")
    forever = config.get("forever", False)
    idle_seconds = int(config.get("idle_sleep_seconds", 300))
    resume = _get_bool(config, "resume_from_checkpoint", False)

    from .ranking_collector import run_ranking_collection
    from .seller_collector import run_seller_collection
    from .category_page_collector import run_category_page_collection

    while True:
        # 检查停止信号
        if stop_flag and stop_flag.is_set():
            print("===== 收到停止信号，流水线终止 =====")
            break

        try:
            if mode in ("ranking", "crawl-top-list-network", "multi-category-network"):
                print("\n===== 阶段1: 榜单采集 =====")
                ranking_stats = run_ranking_collection(config, stop_flag=stop_flag, resume=resume)
                print(f"[榜单采集结果] {ranking_stats}")
            elif mode == "category_page":
                print("\n===== 阶段1: 类目页采集 =====")
                cp_stats = run_category_page_collection(config, stop_flag=stop_flag, resume=resume)
                print(f"[类目页采集结果] {cp_stats}")

            # 阶段间检查停止信号
            if stop_flag and stop_flag.is_set():
                print("===== 收到停止信号，跳过卖家采集 =====")
                break

            print("\n===== 阶段2: 卖家主页采集 =====")
            seller_stats = run_seller_collection(limit=0, stop_flag=stop_flag)
            print(f"[卖家采集结果] {seller_stats}")

        except Exception as exc:
            traceback.print_exc()
            print(f"[ERROR] 采集流水线异常: {exc}")

        if not forever:
            print("===== 采集完成 (非循环模式) =====")
            break

        # 检查停止信号再休眠
        if stop_flag and stop_flag.is_set():
            print("===== 收到停止信号，流水线终止 =====")
            break

        print(f"\n-- 循环休眠 {idle_seconds} 秒后继续... --")
        time.sleep(idle_seconds)


if __name__ == "__main__":
    run_pipeline()
