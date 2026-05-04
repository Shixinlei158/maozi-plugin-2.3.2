from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "root")
    db_name: str = os.getenv("DB_NAME", "ozon_selection")
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


settings = Settings()
