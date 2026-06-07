from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT_DIR / "settings.json"
CONFIG_FILE = ROOT_DIR / "config.json"
CATEGORY_TREE_FILE = ROOT_DIR / "data" / "category_tree.json"


def _load_json_settings() -> dict:
    """加载 settings.json，不存在则返回空字典"""
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_bool(raw: dict, key: str, default: bool = False) -> bool:
    val = raw.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"1", "true", "yes"}
    return bool(val)


@dataclass(frozen=True)
class Settings:
    _raw: dict = field(default_factory=_load_json_settings, repr=False)

    # 数据库
    db_host: str = field(default="127.0.0.1")
    db_port: int = field(default=3306)
    db_user: str = field(default="root")
    db_password: str = field(default="root")
    db_name: str = field(default="ozon_selection")
    db_connect_timeout: int = field(default=60)
    db_read_timeout: int = field(default=600)
    db_write_timeout: int = field(default=600)

    # 毛子ERP
    maozi_token: str = field(default="")
    maozi_username: str = field(default="")
    maozi_password: str = field(default="")
    maozi_base_url: str = field(default="https://api.maozierp.com")
    maozi_plugin_version: str = field(default="2.3.2")
    request_timeout_seconds: int = field(default=30)

    # 飞书通知
    feishu_webhook_url: str = field(default="")

    # Chrome/浏览器
    chrome_profile_dir: str = field(default="profiles/profile-001")
    chrome_extension_dir: str = field(default="../maozi-plugin-2.3.2")
    chrome_extension_id: str = field(default="kifocjelffhjimimdnjohjldolickjaa")
    chrome_executable_path: str = field(default="")
    chrome_channel: str = field(default="chrome")
    chrome_cdp_url: str = field(default="")
    chrome_remote_debugging_port: int = field(default=9222)
    chrome_headless: bool = field(default=False)

    # 业务参数
    rub_to_cny_rate: str = field(default="0.0912")

    # 榜单采集
    top_list_default_page_size: int = field(default=50)
    top_list_sku3_batch_size: int = field(default=60)
    top_list_sku3_batch_concurrency: int = field(default=5)
    top_list_sku3_batch_chunk_delay_ms: int = field(default=500)

    # 卖家采集
    seller_page_timeout_seconds: int = field(default=120)
    seller_sku_workers: int = field(default=6)

    # 筛选规则参数
    seed_create_days_max: int = field(default=200)
    default_create_days_max: int = field(default=180)

    # 循环控制
    collection_idle_sleep_seconds: int = field(default=300)

    def __post_init__(self):
        raw = self._raw
        if not raw:
            return
        object.__setattr__(self, "db_host", str(raw.get("DB_HOST", "127.0.0.1")))
        object.__setattr__(self, "db_port", int(raw.get("DB_PORT", 3306)))
        object.__setattr__(self, "db_user", str(raw.get("DB_USER", "root")))
        object.__setattr__(self, "db_password", str(raw.get("DB_PASSWORD", "root")))
        object.__setattr__(self, "db_name", str(raw.get("DB_NAME", "ozon_selection")))
        object.__setattr__(self, "db_connect_timeout", int(raw.get("DB_CONNECT_TIMEOUT", 60)))
        object.__setattr__(self, "db_read_timeout", int(raw.get("DB_READ_TIMEOUT", 600)))
        object.__setattr__(self, "db_write_timeout", int(raw.get("DB_WRITE_TIMEOUT", 600)))

        object.__setattr__(self, "maozi_token", str(raw.get("MAOZI_TOKEN", "")))
        object.__setattr__(self, "maozi_username", str(raw.get("MAOZI_USERNAME", "")))
        object.__setattr__(self, "maozi_password", str(raw.get("MAOZI_PASSWORD", "")))
        object.__setattr__(self, "maozi_base_url", str(raw.get("MAOZI_BASE_URL", "https://api.maozierp.com")))
        object.__setattr__(self, "maozi_plugin_version", str(raw.get("MAOZI_PLUGIN_VERSION", "2.3.2")))
        object.__setattr__(self, "request_timeout_seconds", int(raw.get("REQUEST_TIMEOUT_SECONDS", 30)))
        object.__setattr__(self, "feishu_webhook_url", str(raw.get("FEISHU_WEBHOOK_URL", "")))

        object.__setattr__(self, "chrome_profile_dir", str(raw.get("CHROME_PROFILE_DIR", "profiles/profile-001")))
        object.__setattr__(self, "chrome_extension_dir", str(raw.get("CHROME_EXTENSION_DIR", "../maozi-plugin-2.3.2")))
        object.__setattr__(self, "chrome_extension_id", str(raw.get("CHROME_EXTENSION_ID", "kifocjelffhjimimdnjohjldolickjaa")))
        object.__setattr__(self, "chrome_executable_path", str(raw.get("CHROME_EXECUTABLE_PATH", "")))
        object.__setattr__(self, "chrome_channel", str(raw.get("CHROME_CHANNEL", "chrome")))
        object.__setattr__(self, "chrome_cdp_url", str(raw.get("CHROME_CDP_URL", "")))
        object.__setattr__(self, "chrome_remote_debugging_port", int(raw.get("CHROME_REMOTE_DEBUGGING_PORT", 9222)))
        object.__setattr__(self, "chrome_headless", _get_bool(raw, "CHROME_HEADLESS"))

        object.__setattr__(self, "rub_to_cny_rate", str(raw.get("RUB_TO_CNY_RATE", "0.0912")))

        object.__setattr__(self, "top_list_sku3_batch_size", int(raw.get("TOP_LIST_SKU3_BATCH_SIZE", 60)))
        object.__setattr__(self, "top_list_sku3_batch_concurrency", int(raw.get("TOP_LIST_SKU3_BATCH_CONCURRENCY", 5)))
        object.__setattr__(self, "top_list_sku3_batch_chunk_delay_ms", int(raw.get("TOP_LIST_SKU3_BATCH_CHUNK_DELAY_MS", 500)))

        object.__setattr__(self, "seller_page_timeout_seconds", int(raw.get("SELLER_PAGE_TIMEOUT_SECONDS", 120)))
        object.__setattr__(self, "seller_sku_workers", int(raw.get("SELLER_SKU_WORKERS", 6)))

        object.__setattr__(self, "seed_create_days_max", int(raw.get("SEED_CREATE_DAYS_MAX", 200)))
        object.__setattr__(self, "default_create_days_max", int(raw.get("DEFAULT_CREATE_DAYS_MAX", 180)))

        object.__setattr__(self, "collection_idle_sleep_seconds", int(raw.get("COLLECTION_IDLE_SLEEP_SECONDS", 300)))


settings = Settings()


def load_runtime_config() -> dict:
    """加载运行时配置(config.json)，不存在则返回空"""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_runtime_config(config: dict) -> None:
    """保存运行时配置到 config.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
