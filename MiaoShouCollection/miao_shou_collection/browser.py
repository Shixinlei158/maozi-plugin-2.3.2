from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from threading import RLock
from typing import Any

from .config import ROOT_DIR, settings


class BrowserClient:
    """浏览器控制客户端"""
    
    def __init__(
        self,
        profile_dir: str | None = None,
        extension_dir: str | None = None,
        executable_path: str | None = None,
        channel: str | None = None,
        proxy_server: str | None = None,
        cdp_url: str | None = None,
        remote_debugging_port: int | None = None,
        headless: bool | None = None,
    ) -> None:
        self.profile_dir = self._resolve_path(profile_dir or settings.chrome_profile_dir)
        self.extension_dir = self._resolve_path(extension_dir or settings.chrome_extension_dir)
        self.extension_id = settings.chrome_extension_id
        self.executable_path = self._resolve_optional_path(executable_path or settings.chrome_executable_path)
        self.channel = settings.chrome_channel if channel is None else channel
        self.proxy_server = settings.chrome_proxy_server if proxy_server is None else proxy_server
        self.cdp_url = settings.chrome_cdp_url if cdp_url is None else cdp_url
        self.remote_debugging_port = (
            settings.chrome_remote_debugging_port if remote_debugging_port is None else remote_debugging_port
        )
        self.headless = settings.chrome_headless if headless is None else headless
        self.launch_display = settings.chrome_launch_display
        self.launch_xauthority = settings.chrome_launch_xauthority
        self.fingerprint_mask_enabled = settings.chrome_fingerprint_mask_enabled
        
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._task_lock = RLock()
        self._prepared_context_ids: set[int] = set()
        self._prepared_page_ids: set[int] = set()
        self._fingerprint_config_cache: dict[str, Any] | None = None
    
    def _resolve_path(self, path_str: str) -> Path:
        """解析路径，支持相对路径"""
        path = Path(path_str)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path.resolve()
    
    def _resolve_optional_path(self, path_str: str) -> Path | None:
        """解析可选路径"""
        if not path_str:
            return None
        return self._resolve_path(path_str)
    
    def launch_real_chrome(self, url: str = "about:blank") -> None:
        """启动真实Chrome浏览器"""
        executable = self.executable_path or self._detect_chrome_executable()
        if not executable or not executable.exists():
            raise RuntimeError(
                "Chrome executable was not found. Set CHROME_EXECUTABLE_PATH or pass --chrome-exe explicitly."
            )
        
        fingerprint = self._fingerprint_config() if self.fingerprint_mask_enabled else None
        args = [
            str(executable),
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={self.remote_debugging_port}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        
        launch_url = url
        if fingerprint:
            args.extend([
                f"--user-agent={fingerprint['user_agent']}",
                f"--window-size={fingerprint['screen_width']},{fingerprint['screen_height']}",
                f"--lang={fingerprint['locale']}",
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist",
                "--disable-infobars",
            ])
            launch_url = "about:blank"
        
        if self.proxy_server:
            args.append(f"--proxy-server={self.proxy_server}")
        else:
            args.append("--no-proxy-server")
        
        if self.extension_dir.exists():
            args.extend([
                f"--load-extension={self.extension_dir}",
            ])
        
        args.append(launch_url)
        subprocess.Popen(args, env=self._chrome_launch_env())
        
        if fingerprint:
            self._bootstrap_real_chrome(url)
    
    def _detect_chrome_executable(self) -> Path | None:
        """检测Chrome可执行文件路径"""
        # Windows常见路径
        possible_paths = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
        
        # 尝试从PATH中查找
        import shutil
        chrome_path = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
        if chrome_path:
            return Path(chrome_path)
        
        return None
    
    def _chrome_launch_env(self) -> dict[str, str]:
        """获取Chrome启动环境变量"""
        env = os.environ.copy()
        if self.launch_display:
            env["DISPLAY"] = self.launch_display
        if self.launch_xauthority:
            env["XAUTHORITY"] = self.launch_xauthority
        return env
    
    def _bootstrap_real_chrome(self, url: str) -> None:
        """引导启动真实Chrome浏览器"""
        cdp_url = self.cdp_url or f"http://127.0.0.1:{self.remote_debugging_port}"
        deadline = time.time() + 20
        last_error: Exception | None = None
        
        while time.time() < deadline:
            try:
                self.connect()
                if self._context:
                    page = self._context.pages[0] if self._context.pages else self._context.new_page()
                    self._prepare_page(page)
                    page.goto(url, wait_until="domcontentloaded", timeout=120000)
                    return
            except Exception as exc:
                last_error = exc
                time.sleep(1)
        
        raise RuntimeError(f"Chrome launched but fingerprint bootstrap over CDP failed: {last_error}")
    
    def connect(self) -> None:
        """连接到现有浏览器"""
        if not self._playwright:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        
        if self.cdp_url:
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
                self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
                self._ensure_context_fingerprint(self._context)
            except Exception as e:
                raise RuntimeError(f"无法连接到Chrome浏览器（CDP: {self.cdp_url}）: {e}")
        else:
            # 启动新浏览器
            self._context = self._launch_context(self._playwright)
    
    def _launch_context(self, playwright: Any) -> Any:
        """启动浏览器上下文"""
        args = ["--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--enable-unsafe-swiftshader"]
        fingerprint = self._fingerprint_config() if self.fingerprint_mask_enabled else None
        
        if fingerprint:
            args.extend([
                f"--user-agent={fingerprint['user_agent']}",
                f"--window-size={fingerprint['screen_width']},{fingerprint['screen_height']}",
                f"--lang={fingerprint['locale']}",
                "--use-gl=swiftshader",
                "--ignore-gpu-blocklist",
            ])
        
        if self.extension_dir.exists():
            args.extend([
                f"--load-extension={self.extension_dir}",
            ])
        
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": args,
        }
        
        if fingerprint:
            kwargs["user_agent"] = fingerprint["user_agent"]
            kwargs["locale"] = fingerprint["locale"]
            kwargs["viewport"] = {
                "width": fingerprint["screen_width"],
                "height": fingerprint["screen_height"],
            }
            kwargs["screen"] = {
                "width": fingerprint["screen_width"],
                "height": fingerprint["screen_height"],
            }
            if fingerprint["timezone"]:
                kwargs["timezone_id"] = fingerprint["timezone"]
        
        if self.executable_path:
            kwargs["executable_path"] = str(self.executable_path)
        elif self.channel:
            kwargs["channel"] = self.channel
        
        if self.proxy_server:
            kwargs["proxy"] = {"server": self.proxy_server}
        else:
            args.append("--no-proxy-server")
        
        context = playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **kwargs,
        )
        self._ensure_context_fingerprint(context)
        return context
    
    def _ensure_context_fingerprint(self, context: Any) -> None:
        """确保上下文应用了指纹伪装"""
        if not self.fingerprint_mask_enabled:
            return
        
        context_id = id(context)
        if context_id in self._prepared_context_ids:
            return
        
        context.add_init_script(self._fingerprint_override_script())
        self._prepared_context_ids.add(context_id)
    
    def _prepare_page(self, page: Any) -> None:
        """准备页面，应用指纹伪装"""
        if not self.fingerprint_mask_enabled:
            return
        
        page_id = id(page)
        if page_id in self._prepared_page_ids:
            return
        
        page.add_init_script(self._fingerprint_override_script())
        self._prepared_page_ids.add(page_id)
    
    def _fingerprint_config(self) -> dict[str, Any]:
        """获取指纹配置"""
        if self._fingerprint_config_cache is not None:
            return self._fingerprint_config_cache
        
        self._fingerprint_config_cache = {
            "user_agent": settings.chrome_fingerprint_user_agent,
            "platform": settings.chrome_fingerprint_platform,
            "platform_label": settings.chrome_fingerprint_platform_label,
            "hardware_concurrency": settings.chrome_fingerprint_hardware_concurrency,
            "device_memory": settings.chrome_fingerprint_device_memory,
            "locale": settings.chrome_fingerprint_locale,
            "timezone": settings.chrome_fingerprint_timezone,
            "screen_width": settings.chrome_fingerprint_screen_width,
            "screen_height": settings.chrome_fingerprint_screen_height,
            "screen_avail_width": settings.chrome_fingerprint_screen_avail_width,
            "screen_avail_height": settings.chrome_fingerprint_screen_avail_height,
            "color_depth": settings.chrome_fingerprint_color_depth,
            "webgl_vendor": settings.chrome_fingerprint_webgl_vendor,
            "webgl_renderer": settings.chrome_fingerprint_webgl_renderer,
        }
        return self._fingerprint_config_cache
    
    def _fingerprint_override_script(self) -> str:
        """生成指纹伪装脚本"""
        config = self._fingerprint_config()
        
        script = f"""
        // 指纹伪装脚本
        (function() {{
            // Navigator属性
            Object.defineProperty(navigator, 'platform', {{ get: () => '{config["platform"]}' }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {config["hardware_concurrency"]} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {config["device_memory"]} }});
            Object.defineProperty(navigator, 'language', {{ get: () => '{config["locale"]}' }});
            Object.defineProperty(navigator, 'languages', {{ get: () => ['{config["locale"]}'] }});
            
            // Screen属性
            Object.defineProperty(screen, 'width', {{ get: () => {config["screen_width"]} }});
            Object.defineProperty(screen, 'height', {{ get: () => {config["screen_height"]} }});
            Object.defineProperty(screen, 'availWidth', {{ get: () => {config["screen_avail_width"]} }});
            Object.defineProperty(screen, 'availHeight', {{ get: () => {config["screen_avail_height"]} }});
            Object.defineProperty(screen, 'colorDepth', {{ get: () => {config["color_depth"]} }});
            
            // WebGL指纹
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) {{
                    return '{config["webgl_vendor"]}';
                }}
                if (parameter === 37446) {{
                    return '{config["webgl_renderer"]}';
                }}
                return getParameter.call(this, parameter);
            }};
        }})();
        """
        return script
    
    def new_page(self) -> Any:
        """创建新页面"""
        if not self._context:
            self.connect()
        
        page = self._context.new_page()
        self._prepare_page(page)
        return page
    
    def get_page(self, url: str | None = None) -> Any:
        """获取页面，如果指定URL则导航到该URL"""
        page = self.new_page()
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return page
    
    def close(self) -> None:
        """关闭浏览器连接"""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()