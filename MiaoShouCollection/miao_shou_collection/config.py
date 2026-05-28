from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """浏览器配置设置"""
    
    # 浏览器配置
    chrome_profile_dir: str = os.getenv("CHROME_PROFILE_DIR", "profiles/profile-001")
    chrome_extension_dir: str = os.getenv("CHROME_EXTENSION_DIR", "../maozi-plugin-2.3.2")
    chrome_extension_id: str = os.getenv("CHROME_EXTENSION_ID", "kifocjelffhjimimdnjohjldolickjaa")
    chrome_executable_path: str = os.getenv("CHROME_EXECUTABLE_PATH", "")
    chrome_channel: str = os.getenv("CHROME_CHANNEL", "chrome")
    chrome_proxy_server: str = os.getenv("CHROME_PROXY_SERVER", "")
    chrome_cdp_url: str = os.getenv("CHROME_CDP_URL", "")
    chrome_remote_debugging_port: int = int(os.getenv("CHROME_REMOTE_DEBUGGING_PORT", "9224"))
    chrome_headless: bool = os.getenv("CHROME_HEADLESS", "false").lower() in {"1", "true", "yes"}
    chrome_launch_display: str = os.getenv("CHROME_LAUNCH_DISPLAY", "")
    chrome_launch_xauthority: str = os.getenv("CHROME_LAUNCH_XAUTHORITY", "")
    
    # 指纹伪装配置
    chrome_fingerprint_mask_enabled: bool = os.getenv("CHROME_FINGERPRINT_MASK_ENABLED", "false").lower() in {
        "1", "true", "yes",
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
    
    # 代理配置
    http_proxy_url: str = os.getenv("HTTP_PROXY_URL", "")
    https_proxy_url: str = os.getenv("HTTPS_PROXY_URL", "")
    
    # 请求配置
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    requests_trust_env: bool = os.getenv("REQUESTS_TRUST_ENV", "false").lower() in {"1", "true", "yes"}


settings = Settings()