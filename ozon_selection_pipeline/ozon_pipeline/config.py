from dataclasses import dataclass
from datetime import date
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

IS_LINUX = sys.platform == "linux"


@dataclass
class DBProfile:
    name: str
    host: str
    port: int = 3306
    user: str = "root"
    password: str = "root"
    database: str = "ozon_selection"

DB_PROFILES: dict[str, DBProfile] = {
    "local": DBProfile(
        name="本机",
        host=os.getenv("DB_HOST_LOCAL", "127.0.0.1"),
        port=int(os.getenv("DB_PORT_LOCAL", "3306")),
        user=os.getenv("DB_USER_LOCAL", "root"),
        password=os.getenv("DB_PASSWORD_LOCAL", "root"),
        database=os.getenv("DB_NAME", "ozon_selection"),
    ),
    "ts-lx": DBProfile(
        name="ts-lx",
        host=os.getenv("DB_HOST_TS", "100.97.110.39"),
        port=int(os.getenv("DB_PORT_TS", "3306")),
        user=os.getenv("DB_USER_TS", "root"),
        password=os.getenv("DB_PASSWORD_TS", "root"),
        database=os.getenv("DB_NAME", "ozon_selection"),
    ),
    "frp-lx": DBProfile(
        name="frp-lx",
        host=os.getenv("DB_HOST_FRP", "127.0.0.1"),
        port=int(os.getenv("DB_PORT_FRP", "13306")),
        user=os.getenv("DB_USER_FRP", "root"),
        password=os.getenv("DB_PASSWORD_FRP", "root"),
        database=os.getenv("DB_NAME", "ozon_selection"),
    ),
}

def _detect_db_profile() -> str:
    """自动检测最优数据库连接。
    如果 .env DB_HOST 明确指向远程 → 用对应 profile；
    否则优先尝试 127.0.0.1:3306（本地/lx同机），不可达则降级。
    """
    db_host = os.getenv("DB_HOST", "")
    # 显式远程地址 → 直接使用
    if db_host.startswith("100."):
        return "ts-lx"
    if db_host and db_host not in ("127.0.0.1", "localhost", ""):
        return "ts-lx"

    # 没有显式远程配置 → 自动探测本地
    local = DB_PROFILES["local"]
    try:
        import pymysql
        conn = pymysql.connect(
            host=local.host, port=local.port,
            user=local.user, password=local.password,
            database=local.database,
            connect_timeout=2, charset="utf8mb4",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return "local"
    except Exception:
        pass
    return "local"

_active_db_profile_key: str = _detect_db_profile()



@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "root")
    db_name: str = os.getenv("DB_NAME", "ozon_selection")
    db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "60"))
    db_read_timeout: int = int(os.getenv("DB_READ_TIMEOUT", "600"))
    db_write_timeout: int = int(os.getenv("DB_WRITE_TIMEOUT", "600"))
    maozi_token: str = os.getenv("MAOZI_TOKEN", "")
    maozi_base_url: str = os.getenv("MAOZI_BASE_URL", "https://api.maozierp.com")
    maozi_plugin_version: str = os.getenv("MAOZI_PLUGIN_VERSION", "2.3.2")
    seller_recollect_days: int = int(os.getenv("SELLER_RECOLLECT_DAYS", "30"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    requests_trust_env: bool = os.getenv("REQUESTS_TRUST_ENV", "false").lower() in {"1", "true", "yes"}
    http_proxy_url: str = os.getenv("HTTP_PROXY_URL", "")
    https_proxy_url: str = os.getenv("HTTPS_PROXY_URL", "")
    chrome_profile_dir: str = os.getenv("CHROME_PROFILE_DIR", "profiles/profile-001")
    chrome_extension_dir: str = os.getenv("CHROME_EXTENSION_DIR", "../maozi-plugin-2.3.2")
    chrome_extension_id: str = os.getenv("CHROME_EXTENSION_ID", "kifocjelffhjimimdnjohjldolickjaa")
    chrome_executable_path: str = os.getenv("CHROME_EXECUTABLE_PATH", "")
    chrome_channel: str = os.getenv("CHROME_CHANNEL", "chrome")
    chrome_proxy_server: str = os.getenv("CHROME_PROXY_SERVER", "")
    chrome_cdp_url: str = os.getenv("CHROME_CDP_URL", "")
    chrome_remote_debugging_port: int = int(os.getenv("CHROME_REMOTE_DEBUGGING_PORT", "9222"))
    chrome_headless: bool = os.getenv("CHROME_HEADLESS", "false").lower() in {"1", "true", "yes"}
    chrome_launch_display: str = os.getenv("CHROME_LAUNCH_DISPLAY", "")
    chrome_launch_xauthority: str = os.getenv("CHROME_LAUNCH_XAUTHORITY", "")
    chrome_fingerprint_mask_enabled: bool = os.getenv("CHROME_FINGERPRINT_MASK_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    chrome_fingerprint_user_agent: str = os.getenv("CHROME_FINGERPRINT_USER_AGENT", "")
    chrome_fingerprint_platform: str = os.getenv("CHROME_FINGERPRINT_PLATFORM", "Win32")
    chrome_fingerprint_platform_label: str = os.getenv("CHROME_FINGERPRINT_PLATFORM_LABEL", "Windows")
    chrome_fingerprint_hardware_concurrency: int = int(
        os.getenv("CHROME_FINGERPRINT_HARDWARE_CONCURRENCY", "16")
    )
    chrome_fingerprint_device_memory: int = int(os.getenv("CHROME_FINGERPRINT_DEVICE_MEMORY", "8"))
    chrome_fingerprint_locale: str = os.getenv("CHROME_FINGERPRINT_LOCALE", "zh-CN")
    chrome_fingerprint_timezone: str = os.getenv("CHROME_FINGERPRINT_TIMEZONE", "")
    chrome_fingerprint_screen_width: int = int(os.getenv("CHROME_FINGERPRINT_SCREEN_WIDTH", "1920"))
    chrome_fingerprint_screen_height: int = int(os.getenv("CHROME_FINGERPRINT_SCREEN_HEIGHT", "1080"))
    chrome_fingerprint_screen_avail_width: int = int(os.getenv("CHROME_FINGERPRINT_SCREEN_AVAIL_WIDTH", "0"))
    chrome_fingerprint_screen_avail_height: int = int(os.getenv("CHROME_FINGERPRINT_SCREEN_AVAIL_HEIGHT", "0"))
    chrome_fingerprint_color_depth: int = int(os.getenv("CHROME_FINGERPRINT_COLOR_DEPTH", "24"))
    chrome_fingerprint_webgl_vendor: str = os.getenv("CHROME_FINGERPRINT_WEBGL_VENDOR", "Google Inc. (NVIDIA)")
    chrome_fingerprint_webgl_renderer: str = os.getenv(
        "CHROME_FINGERPRINT_WEBGL_RENDERER",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    )
    rub_to_cny_rate: str = os.getenv("RUB_TO_CNY_RATE", "0.0912")
    top_list_refresh_hours: int = int(os.getenv("TOP_LIST_REFRESH_HOURS", "24"))
    top_list_recheck_qualified_days: int = int(os.getenv("TOP_LIST_RECHECK_QUALIFIED_DAYS", "7"))
    top_list_recheck_rejected_days: int = int(os.getenv("TOP_LIST_RECHECK_REJECTED_DAYS", "21"))
    top_list_recheck_failed_hours: int = int(os.getenv("TOP_LIST_RECHECK_FAILED_HOURS", "12"))
    top_list_recheck_changed_hours: int = int(os.getenv("TOP_LIST_RECHECK_CHANGED_HOURS", "72"))
    top_list_default_sales_min: str = os.getenv("TOP_LIST_DEFAULT_SALES_MIN", "1")
    top_list_default_sales_max: str = os.getenv("TOP_LIST_DEFAULT_SALES_MAX", "65")
    top_list_default_page_size: int = int(os.getenv("TOP_LIST_DEFAULT_PAGE_SIZE", "50"))
    top_list_default_max_pages: int = int(os.getenv("TOP_LIST_DEFAULT_MAX_PAGES", "100"))
    top_list_default_create_date_from: str = os.getenv("TOP_LIST_DEFAULT_CREATE_DATE_FROM", "2025-04-01")
    top_list_default_create_date_to: str = os.getenv("TOP_LIST_DEFAULT_CREATE_DATE_TO", date.today().isoformat())
    # 毛子 SKU3 批量预取配置 (适用于 top-list 和卖家页)
    top_list_sku3_batch_size: int = int(os.getenv("TOP_LIST_SKU3_BATCH_SIZE", "60"))
    top_list_sku3_batch_concurrency: int = int(os.getenv("TOP_LIST_SKU3_BATCH_CONCURRENCY", "16"))
    top_list_sku3_batch_chunk_delay_ms: int = int(os.getenv("TOP_LIST_SKU3_BATCH_CHUNK_DELAY_MS", "500"))
    top_list_sku3_batch_min_success_rate: float = float(os.getenv("TOP_LIST_SKU3_BATCH_MIN_SUCCESS_RATE", "0.05"))
    top_list_sku3_batch_low_yield_limit: int = int(os.getenv("TOP_LIST_SKU3_BATCH_LOW_YIELD_LIMIT", "1"))

    seed_pool_query_limit: int = int(os.getenv("SEED_POOL_QUERY_LIMIT", "500"))
    seed_sku_workers: int = int(os.getenv("SEED_SKU_WORKERS", "4"))
    seller_sku_workers: int = int(os.getenv("SELLER_SKU_WORKERS", "6"))
    chrome_page_prune_threshold: int = int(os.getenv("CHROME_PAGE_PRUNE_THRESHOLD", "12"))
    seller_page_timeout_seconds: int = int(os.getenv("SELLER_PAGE_TIMEOUT_SECONDS", "120"))
    seller_page_workers: int = int(os.getenv("SELLER_PAGE_WORKERS", "1"))
    seller_fast_mode: bool = os.getenv("SELLER_FAST_MODE", "true").lower() in {"1", "true", "yes"}
    seller_store_deferred_universe: bool = os.getenv("SELLER_STORE_DEFERRED_UNIVERSE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    seller_store_rejected_results: bool = os.getenv("SELLER_STORE_REJECTED_RESULTS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    seller_seed_skus_from_home: bool = os.getenv("SELLER_SEED_SKUS_FROM_HOME", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    seller_fast_fetch_candidate_offers: bool = os.getenv("SELLER_FAST_FETCH_CANDIDATE_OFFERS", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    seller_home_sku_batch_size: int = int(os.getenv("SELLER_HOME_SKU_BATCH_SIZE", "1000"))
    seller_write_minimal: bool = os.getenv("SELLER_WRITE_MINIMAL", "true").lower() in {"1", "true", "yes"}
    collection_idle_sleep_seconds: int = int(os.getenv("COLLECTION_IDLE_SLEEP_SECONDS", "300"))
    collection_cycle_sleep_seconds: int = int(os.getenv("COLLECTION_CYCLE_SLEEP_SECONDS", "60"))
    collection_error_sleep_seconds: int = int(os.getenv("COLLECTION_ERROR_SLEEP_SECONDS", "300"))
    feishu_webhook_url: str = os.getenv("FEISHU_WEBHOOK_URL", "")


settings = Settings()
