from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import subprocess
from pathlib import Path
from threading import RLock
import time
from string import Template
from typing import Any

from .config import ROOT_DIR, settings
from .ozon_frontend import OZON_BASE, parse_seller_home_page, parse_seller_offers_widget
from .util import grams_from_text, percent_text_to_decimal, to_decimal, to_int

MAOZI_SELECTION_URL = "https://ozon.maozierp.com/#/selection/top-list"
MAOZI_SELECTION_ORIGIN = "https://ozon.maozierp.com"
AUTOMATION_PAGE_NAME_PREFIX = "ozon-pipeline:"
MAOZI_CHALLENGE_MARKERS = (
    "正在进行安全验证",
    "本网站使用安全服务防护恶意自动程序",
    "cloudflare",
)


class BrowserOzonClient:
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
        self.profile_dir = resolve_path(profile_dir or settings.chrome_profile_dir)
        self.extension_dir = resolve_path(extension_dir or settings.chrome_extension_dir)
        self.extension_id = settings.chrome_extension_id
        self.executable_path = resolve_optional_path(executable_path or settings.chrome_executable_path)
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
        self._playwright_cm: Any | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._session_owns_context = False
        self._sticky_pages: dict[str, Any] = {}
        self._task_lock = RLock()
        self._prepared_context_ids: set[int] = set()
        self._prepared_page_ids: set[int] = set()
        self._fingerprint_config_cache: dict[str, Any] | None = None
        self._detected_chrome_version: str | None = None
        self._cached_maozi_token: tuple[str, str] | None = None  # (token, source)

    def seller_offers(self, sku: str) -> list[dict[str, Any]]:
        return self._run_page_task(self._fetch_seller_offers, sku)

    def product_snapshot(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_product_snapshot, sku)

    def clone(self) -> "BrowserOzonClient":
        return BrowserOzonClient(
            profile_dir=str(self.profile_dir),
            extension_dir=str(self.extension_dir),
            executable_path=str(self.executable_path) if self.executable_path else None,
            channel=self.channel,
            proxy_server=self.proxy_server,
            cdp_url=self.cdp_url,
            remote_debugging_port=self.remote_debugging_port,
            headless=self.headless,
        )

    def seller_home_products(self, seller_url: str, max_scrolls: int = 8) -> dict[str, Any]:
        return self._run_page_task(self._fetch_seller_home_products, seller_url, max_scrolls=max_scrolls)

    def maozi_sku3(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_maozi_sku3, sku)

    def top_list_sku3(self, sku: str) -> dict[str, Any]:
        result = self.top_list_sku3_batch([sku], concurrency=1)
        payload = result.get(str(sku))
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"top-list sku3 batch did not return payload for sku {sku}")

    def top_list_sku3_batch(self, skus: list[str], concurrency: int | None = None) -> dict[str, dict[str, Any]]:
        normalized: list[str] = []
        seen: set[str] = set()
        for sku in skus:
            text = str(sku).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        if not normalized:
            return {}
        resolved_concurrency = max(1, int(concurrency or settings.top_list_sku3_batch_concurrency))
        return self._run_page_task(self._fetch_top_list_sku3_batch, normalized, resolved_concurrency)

    def plugin_card_snapshot(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_plugin_card_snapshot, sku)

    def top_list_page(self, filters: dict[str, Any], page_no: int, page_size: int = 50) -> dict[str, Any]:
        return self._run_page_task(self._fetch_top_list_page, filters, page_no, page_size)

    def describe(self) -> dict[str, Any]:
        resolved_executable = self.executable_path or detect_chrome_executable()
        info = {
            "profile_dir": str(self.profile_dir),
            "extension_dir": str(self.extension_dir),
            "extension_exists": self.extension_dir.exists(),
            "extension_id": self.extension_id,
            "executable_path": str(resolved_executable) if resolved_executable else "<not-found>",
            "executable_exists": resolved_executable.exists() if resolved_executable else False,
            "channel": self.channel or "<playwright-default>",
            "proxy_server": self.proxy_server or "<none>",
            "cdp_url": self.cdp_url or "<none>",
            "remote_debugging_port": self.remote_debugging_port,
            "headless": self.headless,
            "launch_display": self.launch_display or "<inherit>",
            "launch_xauthority": self.launch_xauthority or "<inherit>",
            "fingerprint_mask_enabled": self.fingerprint_mask_enabled,
        }
        if self.fingerprint_mask_enabled:
            fingerprint = self._fingerprint_config()
            info.update(
                {
                    "fingerprint_user_agent": fingerprint["user_agent"],
                    "fingerprint_platform": fingerprint["platform"],
                    "fingerprint_locale": fingerprint["locale"],
                    "fingerprint_hardware_concurrency": fingerprint["hardware_concurrency"],
                    "fingerprint_device_memory": fingerprint["device_memory"],
                    "fingerprint_webgl_vendor": fingerprint["webgl_vendor"],
                    "fingerprint_webgl_renderer": fingerprint["webgl_renderer"],
                    "fingerprint_screen": (
                        f"{fingerprint['screen_width']}x{fingerprint['screen_height']}"
                    ),
                }
            )
        return info

    def ping_cdp(self) -> dict[str, Any]:
        if not self.cdp_url:
            return {"reachable": False, "reason": "cdp_not_configured"}
        if self._context is not None:
            pages = [page.url for page in self._context.pages]
            return {
                "reachable": True,
                "cdp_url": self.cdp_url,
                "context_count": 1,
                "page_count": len(pages),
                "pages": pages,
            }
        sync_playwright = import_sync_playwright()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            contexts = browser.contexts
            pages = []
            for context in contexts:
                for page in context.pages:
                    pages.append(page.url)
            return {
                "reachable": True,
                "cdp_url": self.cdp_url,
                "context_count": len(contexts),
                "page_count": len(pages),
                "pages": pages,
            }

    @contextmanager
    def session(self):
        self.open_session()
        try:
            yield self
        finally:
            self.close_session()

    def open_session(self) -> None:
        if self._playwright is not None:
            return
        sync_playwright = import_sync_playwright()
        self._playwright_cm = sync_playwright()
        self._playwright = self._playwright_cm.__enter__()
        if self.cdp_url:
            launched = False
            for attempt in range(6):
                try:
                    self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
                    break
                except Exception:
                    if not launched:
                        self.launch_real_chrome()
                        launched = True
                    delay = 3 + attempt * 3
                    if attempt < 5:
                        time.sleep(delay)
                    else:
                        raise RuntimeError(
                            f"无法连接到 Chrome 浏览器（CDP: {self.cdp_url}）。"
                            f"请确认 Chrome 已启动且远程调试端口已开启。"
                        ) from None
            existing_contexts = list(self._browser.contexts)
            if existing_contexts:
                self._context = existing_contexts[0]
                self._session_owns_context = False
                self._ensure_context_fingerprint(self._context)
                self._prune_unused_pages(self._context, force=True)
            else:
                self._context = self._browser.new_context()
                self._session_owns_context = True
                self._ensure_context_fingerprint(self._context)
            return
        self._context = self._launch_context(self._playwright)
        self._session_owns_context = True

    def close_session(self) -> None:
        self._cached_maozi_token = None
        try:
            for page in list(self._sticky_pages.values()):
                try:
                    if page is not None and not page.is_closed():
                        page.close()
                except Exception:
                    pass
            self._sticky_pages.clear()
            if self._context is not None and self._session_owns_context:
                try:
                    self._context.close()
                except Exception:
                    pass
        finally:
            self._context = None
            self._session_owns_context = False
            self._browser = None
            self._prepared_context_ids.clear()
            self._prepared_page_ids.clear()
            if self._playwright_cm is not None:
                try:
                    self._playwright_cm.__exit__(None, None, None)
                finally:
                    self._playwright_cm = None
                    self._playwright = None

    def launch_real_chrome(self, url: str = OZON_BASE) -> None:
        executable = self.executable_path or detect_chrome_executable()
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
            args.extend(
                [
                    f"--user-agent={fingerprint['user_agent']}",
                    f"--window-size={fingerprint['screen_width']},{fingerprint['screen_height']}",
                    f"--lang={fingerprint['locale']}",
                    "--use-gl=swiftshader",
                    "--enable-unsafe-swiftshader",
                    "--ignore-gpu-blocklist",
                    "--disable-infobars",
                ]
            )
            launch_url = "about:blank"
        if self.proxy_server:
            args.append(f"--proxy-server={self.proxy_server}")
        if self.extension_dir.exists():
            args.extend(
                [
                    f"--load-extension={self.extension_dir}",
                ]
            )
        args.append(launch_url)
        subprocess.Popen(args, env=self._chrome_launch_env())
        if fingerprint:
            self._bootstrap_real_chrome(url)

    def _launch_context(self, playwright: Any):
        args = ["--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--enable-unsafe-swiftshader"]
        fingerprint = self._fingerprint_config() if self.fingerprint_mask_enabled else None
        if fingerprint:
            args.extend(
                [
                    f"--user-agent={fingerprint['user_agent']}",
                    f"--window-size={fingerprint['screen_width']},{fingerprint['screen_height']}",
                    f"--lang={fingerprint['locale']}",
                    "--use-gl=swiftshader",
                    "--ignore-gpu-blocklist",
                ]
            )
        if self.extension_dir.exists():
            args.extend(
                [
                    f"--load-extension={self.extension_dir}",
                ]
            )
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
        context = playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **kwargs,
        )
        self._ensure_context_fingerprint(context)
        return context

    def _run_page_task(self, handler: Any, *args: Any, **kwargs: Any) -> Any:
        with self._task_lock:
            if self._context is not None:
                if self.cdp_url:
                    page, owned = self._acquire_cdp_page(self._context, handler)
                else:
                    page, owned = self._context.new_page(), True
                try:
                    self._prepare_page(page)
                    return handler(page, *args, **kwargs)
                finally:
                    if owned:
                        try:
                            page.close()
                        except Exception:
                            pass
                    if self.cdp_url:
                        self._prune_unused_pages(self._context, current_page=page)

            sync_playwright = import_sync_playwright()

            with sync_playwright() as p:
                if self.cdp_url:
                    launched = False
                    for attempt in range(6):
                        try:
                            browser = p.chromium.connect_over_cdp(self.cdp_url)
                            break
                        except Exception:
                            if not launched:
                                self.launch_real_chrome()
                                launched = True
                            delay = 3 + attempt * 3
                            if attempt < 5:
                                time.sleep(delay)
                            else:
                                raise RuntimeError(
                                    f"无法连接到 Chrome 浏览器（CDP: {self.cdp_url}）。"
                                    f"请确认 Chrome 已启动且远程调试端口已开启。"
                                ) from None

                    try:
                        context = browser.contexts[0] if browser.contexts else browser.new_context()
                        self._ensure_context_fingerprint(context)
                        page, owned = self._acquire_cdp_page(context, handler)
                        try:
                            self._prepare_page(page)
                            return handler(page, *args, **kwargs)
                        finally:
                            if owned:
                                try:
                                    page.close()
                                except Exception:
                                    pass
                            self._prune_unused_pages(context, current_page=page)
                    except Exception as exc:
                        raise RuntimeError(
                            f"cdp page task failed for {getattr(handler, '__name__', 'handler')}: {exc}"
                        ) from exc
                context = self._launch_context(p)
                try:
                    page = context.new_page()
                    self._prepare_page(page)
                    return handler(page, *args, **kwargs)
                finally:
                    context.close()

    def _chrome_launch_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.launch_display:
            env["DISPLAY"] = self.launch_display
        if self.launch_xauthority:
            env["XAUTHORITY"] = self.launch_xauthority
        return env

    def _bootstrap_real_chrome(self, url: str) -> None:
        cdp_url = self.cdp_url or f"http://127.0.0.1:{self.remote_debugging_port}"
        deadline = time.time() + 20
        last_error: Exception | None = None
        while time.time() < deadline:
            sync_playwright = import_sync_playwright()
            with sync_playwright() as p:
                try:
                    browser = p.chromium.connect_over_cdp(cdp_url)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    self._ensure_context_fingerprint(context)
                    page = context.pages[0] if context.pages else context.new_page()
                    self._prepare_page(page)
                    page.goto(url, wait_until="domcontentloaded", timeout=120000)
                    return
                except Exception as exc:
                    last_error = exc
                    time.sleep(1)
        raise RuntimeError(f"Chrome launched but fingerprint bootstrap over CDP failed: {last_error}")

    def _ensure_context_fingerprint(self, context: Any) -> None:
        if not self.fingerprint_mask_enabled:
            return
        context_id = id(context)
        if context_id in self._prepared_context_ids:
            return
        context.add_init_script(self._fingerprint_override_script())
        self._prepared_context_ids.add(context_id)

    def _prepare_page(self, page: Any) -> None:
        try:
            page.bring_to_front()
        except Exception:
            pass

        if not self.fingerprint_mask_enabled:
            return
        self._ensure_context_fingerprint(page.context)
        page_id = id(page)
        if page_id in self._prepared_page_ids:
            return

        fingerprint = self._fingerprint_config(page=page)
        try:
            session = page.context.new_cdp_session(page)
            session.send(
                "Emulation.setUserAgentOverride",
                {
                    "userAgent": fingerprint["user_agent"],
                    "acceptLanguage": fingerprint["accept_language"],
                    "platform": fingerprint["platform_label"],
                    "userAgentMetadata": {
                        "brands": fingerprint["brands"],
                        "fullVersionList": fingerprint["full_version_list"],
                        "platform": fingerprint["platform_label"],
                        "platformVersion": "10.0.0",
                        "architecture": "x86",
                        "model": "",
                        "mobile": False,
                        "bitness": "64",
                        "wow64": False,
                    },
                },
            )
        except Exception:
            pass

        try:
            page.evaluate(self._fingerprint_override_script())
        except Exception:
            pass

        self._prepared_page_ids.add(page_id)

    def _fingerprint_config(self, page: Any | None = None) -> dict[str, Any]:
        if self._fingerprint_config_cache is not None:
            return self._fingerprint_config_cache

        locale = (settings.chrome_fingerprint_locale or "zh-CN").strip()
        primary_language = locale.split(",", 1)[0].split("-", 1)[0] or "zh"
        accept_language = f"{locale},{primary_language};q=0.9" if primary_language != locale else locale
        version = self._resolve_chrome_version(page=page)
        full_version = parse_chrome_version(settings.chrome_fingerprint_user_agent) or version or "136.0.0.0"
        major_version = full_version.split(".", 1)[0]
        user_agent = (
            settings.chrome_fingerprint_user_agent.strip()
            or build_windows_chrome_user_agent(full_version)
        )
        screen_width = max(1280, int(settings.chrome_fingerprint_screen_width))
        screen_height = max(720, int(settings.chrome_fingerprint_screen_height))
        avail_width = settings.chrome_fingerprint_screen_avail_width or screen_width
        avail_height = settings.chrome_fingerprint_screen_avail_height or max(screen_height - 40, 600)
        self._fingerprint_config_cache = {
            "user_agent": user_agent,
            "platform": settings.chrome_fingerprint_platform,
            "platform_label": settings.chrome_fingerprint_platform_label,
            "hardware_concurrency": max(2, int(settings.chrome_fingerprint_hardware_concurrency)),
            "device_memory": max(2, int(settings.chrome_fingerprint_device_memory)),
            "locale": locale,
            "accept_language": accept_language,
            "timezone": settings.chrome_fingerprint_timezone.strip(),
            "screen_width": screen_width,
            "screen_height": screen_height,
            "screen_avail_width": max(800, int(avail_width)),
            "screen_avail_height": max(600, int(avail_height)),
            "color_depth": max(16, int(settings.chrome_fingerprint_color_depth)),
            "webgl_vendor": settings.chrome_fingerprint_webgl_vendor,
            "webgl_renderer": settings.chrome_fingerprint_webgl_renderer,
            "full_version": full_version,
            "major_version": major_version,
            "brands": [
                {"brand": "Chromium", "version": major_version},
                {"brand": "Google Chrome", "version": major_version},
                {"brand": "Not.A/Brand", "version": "24"},
            ],
            "full_version_list": [
                {"brand": "Chromium", "version": full_version},
                {"brand": "Google Chrome", "version": full_version},
                {"brand": "Not.A/Brand", "version": "24.0.0.0"},
            ],
        }
        return self._fingerprint_config_cache

    def _fingerprint_override_script(self) -> str:
        fingerprint = self._fingerprint_config()
        payload = json.dumps(
            {
                "userAgent": fingerprint["user_agent"],
                "platform": fingerprint["platform"],
                "platformLabel": fingerprint["platform_label"],
                "hardwareConcurrency": fingerprint["hardware_concurrency"],
                "deviceMemory": fingerprint["device_memory"],
                "locale": fingerprint["locale"],
                "languages": [fingerprint["locale"], fingerprint["locale"].split("-", 1)[0]],
                "screenWidth": fingerprint["screen_width"],
                "screenHeight": fingerprint["screen_height"],
                "screenAvailWidth": fingerprint["screen_avail_width"],
                "screenAvailHeight": fingerprint["screen_avail_height"],
                "colorDepth": fingerprint["color_depth"],
                "webglVendor": fingerprint["webgl_vendor"],
                "webglRenderer": fingerprint["webgl_renderer"],
                "brands": fingerprint["brands"],
                "fullVersionList": fingerprint["full_version_list"],
                "majorVersion": fingerprint["major_version"],
                "fullVersion": fingerprint["full_version"],
            },
            ensure_ascii=True,
        )
        return Template(
            """
(() => {{
  const cfg = $payload;
  const appVersion = cfg.userAgent.replace(/^Mozilla\\//, '');
  const highEntropy = {{
    architecture: 'x86',
    bitness: '64',
    brands: cfg.brands,
    fullVersionList: cfg.fullVersionList,
    mobile: false,
    model: '',
    platform: cfg.platformLabel,
    platformVersion: '10.0.0',
    uaFullVersion: cfg.fullVersion,
    wow64: false,
  }};

  function overrideGetter(target, key, getter) {{
    if (!target) {{
      return;
    }}
    try {{
      Object.defineProperty(target, key, {{
        configurable: true,
        get: getter,
      }});
    }} catch (error) {{
    }}
  }}

  const navigatorProto = Object.getPrototypeOf(navigator);
  overrideGetter(navigatorProto, 'platform', () => cfg.platform);
  overrideGetter(navigatorProto, 'userAgent', () => cfg.userAgent);
  overrideGetter(navigatorProto, 'appVersion', () => appVersion);
  overrideGetter(navigatorProto, 'hardwareConcurrency', () => cfg.hardwareConcurrency);
  overrideGetter(navigatorProto, 'deviceMemory', () => cfg.deviceMemory);
  overrideGetter(navigatorProto, 'language', () => cfg.locale);
  overrideGetter(navigatorProto, 'languages', () => cfg.languages.slice());
  overrideGetter(navigatorProto, 'vendor', () => 'Google Inc.');
  overrideGetter(navigatorProto, 'maxTouchPoints', () => 0);
  overrideGetter(navigatorProto, 'webdriver', () => false);
  overrideGetter(navigatorProto, 'userAgentData', () => ({
    brands: cfg.brands,
    mobile: false,
    platform: cfg.platformLabel,
    toJSON() {{
      return {{
        brands: cfg.brands,
        mobile: false,
        platform: cfg.platformLabel,
      }};
    }},
    getHighEntropyValues(hints) {{
      const response = {{}};
      for (const hint of hints || []) {{
        if (Object.prototype.hasOwnProperty.call(highEntropy, hint)) {{
          response[hint] = highEntropy[hint];
        }}
      }}
      return Promise.resolve(response);
    }},
  }));

  const screenProto = Object.getPrototypeOf(screen);
  overrideGetter(screenProto, 'width', () => cfg.screenWidth);
  overrideGetter(screenProto, 'height', () => cfg.screenHeight);
  overrideGetter(screenProto, 'availWidth', () => cfg.screenAvailWidth);
  overrideGetter(screenProto, 'availHeight', () => cfg.screenAvailHeight);
  overrideGetter(screenProto, 'colorDepth', () => cfg.colorDepth);
  overrideGetter(screenProto, 'pixelDepth', () => cfg.colorDepth);

  function patchWebGL(ctor) {{
    if (!ctor || !ctor.prototype || typeof ctor.prototype.getParameter !== 'function') {{
      return;
    }}
    const original = ctor.prototype.getParameter;
    ctor.prototype.getParameter = function(parameter) {{
      if (parameter === 37445) {{
        return cfg.webglVendor;
      }}
      if (parameter === 37446) {{
        return cfg.webglRenderer;
      }}
      return original.call(this, parameter);
    }};
  }}

  patchWebGL(globalThis.WebGLRenderingContext);
  patchWebGL(globalThis.WebGL2RenderingContext);
}})();
"""
        ).substitute(payload=payload).replace("{{", "{").replace("}}", "}")

    def _resolve_chrome_version(self, page: Any | None = None) -> str | None:
        if self._detected_chrome_version:
            return self._detected_chrome_version

        if page is not None:
            try:
                page_user_agent = page.evaluate("() => navigator.userAgent")
                version = parse_chrome_version(str(page_user_agent))
                if version:
                    self._detected_chrome_version = version
                    return version
            except Exception:
                pass

        executable = self.executable_path or detect_chrome_executable()
        if executable and executable.exists():
            try:
                result = subprocess.run(
                    [str(executable), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                version = parse_chrome_version((result.stdout or "") + (result.stderr or ""))
                if version:
                    self._detected_chrome_version = version
                    return version
            except Exception:
                pass

        self._detected_chrome_version = "136.0.0.0"
        return self._detected_chrome_version

    def _acquire_cdp_page(self, context: Any, handler: Any):
        handler_name = getattr(handler, "__name__", "")
        pages = list(context.pages)

        sticky_handlers = {
            "_fetch_top_list_page",
            "_fetch_top_list_sku3_batch",
            "_fetch_maozi_sku3",
            "_fetch_seller_offers",
            "_fetch_product_snapshot",
            "_fetch_plugin_card_snapshot",
            "_fetch_seller_home_products",
            "_fetch_seller_home_products_api",
        }
        if handler_name in sticky_handlers:
            cached = self._sticky_pages.get(handler_name)
            if cached is not None:
                try:
                    if not cached.is_closed():
                        return cached, False
                except Exception:
                    pass
                self._sticky_pages.pop(handler_name, None)

            if handler_name == "_fetch_maozi_sku3":
                extension_prefix = f"chrome-extension://{self.extension_id}/"
                for page in pages:
                    try:
                        if page.url.startswith(extension_prefix):
                            self._sticky_pages[handler_name] = page
                            return page, False
                    except Exception:
                        continue

            page = context.new_page()
            self._mark_managed_page(page, handler_name)
            self._sticky_pages[handler_name] = page
            return page, False

        # Prefer reusing a disposable tab so we do not disturb the user's live Ozon pages.
        for page in pages:
            if page.url.startswith(("about:blank", "chrome-error://")):
                return page, False

        if handler_name in {"_fetch_top_list_page", "_fetch_maozi_sku3"}:
            for page in pages:
                if page.url.startswith(MAOZI_SELECTION_ORIGIN):
                    return page, False

        if handler_name in {"_fetch_seller_offers", "_fetch_seller_home_products", "_fetch_seller_home_products_api"}:
            for page in pages:
                if page.url.startswith(OZON_BASE):
                    return page, False

        for page in pages:
            if page.url.startswith(OZON_BASE):
                return page, False

        if handler_name == "_fetch_maozi_sku3":
            extension_prefix = f"chrome-extension://{self.extension_id}/"
            for page in pages:
                if page.url.startswith(extension_prefix):
                    return page, False

        page = context.new_page()
        self._mark_managed_page(page, handler_name)
        return page, True

    def _mark_managed_page(self, page: Any, handler_name: str) -> None:
        try:
            if page.is_closed():
                return
            page.evaluate(
                """
                (name) => {
                  try {
                    window.name = name;
                  } catch (error) {
                  }
                }
                """,
                f"{AUTOMATION_PAGE_NAME_PREFIX}{handler_name}",
            )
        except Exception:
            pass

    def _page_marker(self, page: Any) -> str:
        try:
            if page.is_closed():
                return ""
            return str(page.evaluate("() => window.name || ''") or "")
        except Exception:
            return ""

    def _prune_unused_pages(self, context: Any, current_page: Any | None = None, force: bool = False) -> None:
        try:
            pages = list(context.pages)
        except Exception:
            return
        if not force and len(pages) <= settings.chrome_page_prune_threshold:
            return

        keep_pages = {page for page in self._sticky_pages.values() if page is not None}
        if current_page is not None:
            keep_pages.add(current_page)
        popup_url = self._extension_popup_url()

        for page in pages:
            if page in keep_pages:
                continue
            try:
                if page.is_closed():
                    continue
            except Exception:
                continue

            try:
                url = str(page.url or "")
            except Exception:
                url = ""

            should_close = False
            if url.startswith(("about:blank", "chrome-error://")):
                should_close = True
            elif url == popup_url:
                should_close = True
            else:
                marker = self._page_marker(page)
                if marker.startswith(AUTOMATION_PAGE_NAME_PREFIX):
                    should_close = True

            if not should_close:
                continue

            try:
                page.close()
            except Exception:
                pass

    def _fetch_seller_offers(self, page: Any, sku: str) -> list[dict[str, Any]]:
        if not page.url.startswith(OZON_BASE):
            page.goto(OZON_BASE, wait_until="domcontentloaded")
        data = page.evaluate(
            """
            async ({ sku, timeoutMs }) => {
              const target = `/modal/otherOffersFromSellers?product_id=${sku}`;
              const url = `/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(target)}`;
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), timeoutMs);
              const response = await fetch(url, { credentials: "include", signal: controller.signal });
              clearTimeout(timer);
              if (!response.ok) {
                throw new Error(`Ozon seller offers request failed: ${response.status}`);
              }
              return await response.json();
            }
            """,
            {"sku": str(sku), "timeoutMs": 15000},
        )
        return parse_seller_offers_widget(data)

    def _fetch_product_snapshot(self, page: Any, sku: str) -> dict[str, Any]:
        page.goto(f"{OZON_BASE}/product/{sku}/", wait_until="domcontentloaded")
        raw = page.evaluate(
            """
            () => {
              function collectJsonLd() {
                const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                const values = [];
                for (const script of scripts) {
                  try {
                    values.push(JSON.parse(script.textContent || 'null'));
                  } catch (error) {
                  }
                }
                return values;
              }

              function walk(node, visit) {
                if (!node) return;
                if (Array.isArray(node)) {
                  for (const item of node) walk(item, visit);
                  return;
                }
                if (typeof node !== 'object') return;
                visit(node);
                for (const value of Object.values(node)) walk(value, visit);
              }

              const metas = {};
              for (const meta of Array.from(document.querySelectorAll('meta'))) {
                const key = meta.getAttribute('property') || meta.getAttribute('name') || meta.getAttribute('itemprop');
                if (!key) continue;
                metas[key] = meta.getAttribute('content') || '';
              }

              const jsonLd = collectJsonLd();
              let ldPrice = null;
              let ldCurrency = null;
              let ldImage = null;
              let ldTitle = null;

              walk(jsonLd, (node) => {
                if (!ldTitle && typeof node.name === 'string') ldTitle = node.name;
                if (!ldImage && typeof node.image === 'string') ldImage = node.image;
                if (!ldPrice && typeof node.price !== 'undefined') ldPrice = String(node.price);
                if (!ldCurrency && typeof node.priceCurrency === 'string') ldCurrency = node.priceCurrency;
                if (!ldPrice && typeof node.lowPrice !== 'undefined') ldPrice = String(node.lowPrice);
              });

              return {
                title:
                  document.querySelector('h1')?.textContent?.trim() ||
                  metas['og:title'] ||
                  ldTitle ||
                  document.title ||
                  null,
                price_text:
                  metas['product:price:amount'] ||
                  metas['price'] ||
                  ldPrice ||
                  null,
                currency:
                  metas['product:price:currency'] ||
                  ldCurrency ||
                  null,
                main_image_url:
                  metas['og:image'] ||
                  ldImage ||
                  null,
                product_url: window.location.href,
                raw: {
                  metas,
                  jsonLd,
                },
              };
            }
            """
        )
        return {
            "sku": str(sku),
            "title": raw.get("title"),
            "price": to_decimal(raw.get("price_text")),
            "currency": raw.get("currency") or ("RUB" if raw.get("price_text") else None),
            "main_image_url": raw.get("main_image_url"),
            "product_url": raw.get("product_url"),
            "raw": raw,
        }

    def _fetch_maozi_sku3(self, page: Any, sku: str) -> dict[str, Any]:
        errors: list[str] = []

        try:
            target_url = self._extension_popup_url()
            current_url = ""
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            if current_url != target_url:
                page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(300)
            result = page.evaluate(
                """
                async ({ sku, pluginVersion }) => {
                  const storage = await chrome.storage.local.get(["maozierp-token"]);
                  const token = storage["maozierp-token"];
                  if (!token) {
                    throw new Error("maozierp-token is missing in chrome.storage.local");
                  }
                  const response = await fetch(`https://api.maozierp.com/api.chrome/sku3?sku=${sku}`, {
                    method: "POST",
                    headers: {
                      "Authorization": `Bearer ${token}`,
                      "Client": "plugin",
                      "Plugin-Version": pluginVersion,
                      "Content-Type": "application/json",
                      "User-Agent": "Mozilla/5.0"
                    },
                    body: JSON.stringify({ sku: String(sku) })
                  });
                  const text = await response.text();
                  let data = null;
                  try {
                    data = JSON.parse(text);
                  } catch (error) {
                  }
                  return {
                    ok: response.ok,
                    status: response.status,
                    text,
                    data
                  };
                }
                """,
                {"sku": str(sku), "pluginVersion": settings.maozi_plugin_version},
            )
            if result.get("ok"):
                data = result.get("data")
                if isinstance(data, dict):
                    return data
                errors.append("extension token returned non-JSON payload")
            else:
                response_text = str(result.get("text") or "")
                lowered = response_text.lower()
                if any(marker.lower() in lowered for marker in MAOZI_CHALLENGE_MARKERS):
                    print(f"WARN: SKU3 single request returned Cloudflare page, skipping (sku={sku})")
                else:
                    errors.append(f"extension token request failed with HTTP {result.get('status')}: {response_text}")
        except Exception as exc:
            errors.append(f"extension token request error: {exc}")

        try:
            self._ensure_maozi_selection_ready(page)
            result = page.evaluate(
                """
                async ({ sku }) => {
                  const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                  const token = access.accessToken || '';
                  if (!token) {
                    throw new Error('maozierp-core-access.accessToken is missing');
                  }
                  const response = await fetch(`https://api.maozierp.com/api.chrome/sku3?sku=${sku}`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                      'Accept': 'application/json, text/plain, */*',
                      'Authorization': `Bearer ${token}`,
                      'Client': 'pc',
                      'Content-Type': 'application/json',
                      'DNT': '1'
                    },
                    body: JSON.stringify({ sku: String(sku) })
                  });
                  const text = await response.text();
                  let data = null;
                  try {
                    data = JSON.parse(text);
                  } catch (error) {
                  }
                  return {
                    ok: response.ok,
                    status: response.status,
                    text,
                    data
                  };
                }
                """,
                {"sku": str(sku)},
            )
            if result.get("ok"):
                data = result.get("data")
                if isinstance(data, dict):
                    return data
                errors.append("site token returned non-JSON payload")
            else:
                response_text = str(result.get("text") or "")
                lowered = response_text.lower()
                if any(marker.lower() in lowered for marker in MAOZI_CHALLENGE_MARKERS):
                    print(f"WARN: SKU3 site-token path returned Cloudflare page, skipping (sku={sku})")
                else:
                    errors.append(f"site token request failed with HTTP {result.get('status')}: {response_text}")
        except Exception as exc:
            errors.append(f"site token request error: {exc}")

        raise RuntimeError("; ".join(errors))
    def _fetch_top_list_sku3_batch(
        self,
        page: Any,
        skus: list[str],
        concurrency: int,
    ) -> dict[str, dict[str, Any]]:
        # 导航到 ext popup（取 chrome.storage token 的唯一可靠入口）
        target_url = self._extension_popup_url()
        try:
            current = page.url or ""
        except Exception:
            current = ""
        if current != target_url:
            page.goto(target_url, wait_until="load", timeout=15000)
            page.wait_for_timeout(800)

        timeout_ms = min(max(settings.request_timeout_seconds * 1000, 5000), 30000)
        raw = page.evaluate(
            """
            async ({ skus, concurrency, timeoutMs, pluginVersion }) => {
              let token = null;
              for (let i = 0; i < 8 && !token; i++) {
                if (i > 0) await new Promise(r => setTimeout(r, 500));
                try {
                  const s = await chrome.storage.local.get(["maozierp-token"]);
                  token = s["maozierp-token"];
                } catch(e) {}
              }
              if (!token) return {};

              const items = Array.from(new Set((skus || []).map(sku => String(sku).trim()).filter(Boolean)));
              const results = {};
              let nextIndex = 0;
              const workerTotal = Math.max(1, Math.min(Number(concurrency)||1, items.length));

              async function fetchOne(sku) {
                const ctrl = new AbortController();
                const t = setTimeout(() => ctrl.abort(), timeoutMs);
                try {
                  const r = await fetch(`https://api.maozierp.com/api.chrome/sku3?sku=${encodeURIComponent(sku)}`, {
                    method:'POST', credentials:'include', signal:ctrl.signal,
                    headers:{'Accept':'application/json','Authorization':`Bearer ${token}`,'Client':'plugin','Plugin-Version':pluginVersion,'Content-Type':'application/json','User-Agent':'Mozilla/5.0'},
                    body:JSON.stringify({sku:String(sku)})
                  });
                  const text = await r.text(); let data = null; try{data=JSON.parse(text);}catch(e){}
                  results[sku]={ok:r.ok,status:r.status,text,data};
                } catch(err) {
                  results[sku]={ok:false,status:0,text:'',data:null,error:String(err?.message||err)};
                } finally { clearTimeout(t); }
              }
              async function worker(){while(true){const i=nextIndex++;if(i>=items.length)return;await fetchOne(items[i]);}}
              const s = Date.now();
              await Promise.all(Array.from({length:workerTotal},()=>worker()));
              return {results,elapsedMs:Date.now()-s};
            }
            """,
            {
                "skus": [str(s) for s in skus],
                "concurrency": int(concurrency),
                "timeoutMs": timeout_ms,
                "pluginVersion": settings.maozi_plugin_version,
            },
        )

        if not isinstance(raw, dict) or "results" not in raw:
            return {}
        results: dict[str, dict[str, Any]] = {}
        for sku in skus:
            payload = (raw["results"] or {}).get(str(sku))
            if not isinstance(payload, dict):
                continue
            if payload.get("ok") and isinstance(payload.get("data"), dict):
                results[str(sku)] = payload["data"]
        return results

    def _fetch_plugin_card_snapshot(self, page: Any, sku: str) -> dict[str, Any]:
        page.goto(f"{OZON_BASE}/product/{sku}/", wait_until="domcontentloaded", timeout=120000)
        try:
            page.wait_for_function(
                """
                () => {
                  const text = document.body?.innerText || '';
                  return text.includes('类目：') || text.includes('品牌：') || text.includes('月销量：');
                }
                """,
                timeout=5000,
            )
        except Exception:
            pass
        body_text = page.evaluate("() => document.body.innerText || ''")
        card = parse_plugin_card_text(body_text)
        card["raw_text"] = body_text
        return card

    def _fetch_seller_home_products(self, page: Any, seller_url: str, max_scrolls: int = 8) -> dict[str, Any]:
        # Implementation of 3-minute global timeout for single seller page
        deadline = time.time() + 180  # 3 minutes
        try:
            return self._fetch_seller_home_products_api(page, seller_url, deadline=deadline)
        except Exception as exc:
            if time.time() > deadline:
                raise RuntimeError(f"Seller page processing timed out (3min limit): {seller_url}") from exc
            # Fallback to DOM scroll if API failed but time remains
            pass
        return self._fetch_seller_home_products_dom(page, seller_url, max_scrolls=max_scrolls, deadline=deadline)

    def _fetch_seller_home_products_api(self, page: Any, seller_url: str, deadline: float | None = None) -> dict[str, Any]:
        # Must be on an Ozon page for same-origin credentials to work.
        # Direct navigation to seller page triggers antibot → go through main page first,
        # then navigate to seller for visual feedback.
        if not page.url.startswith(OZON_BASE):
            remaining = (deadline - time.time()) * 1000 if deadline else 120000
            if remaining <= 0:
                raise RuntimeError("Timeout before navigation")
            page.goto(OZON_BASE, wait_until="domcontentloaded", timeout=min(remaining, 30000))
            page.wait_for_timeout(500)
        # Navigate to seller page for visual feedback (user can see progress)
        if page.url != seller_url:
            remaining = (deadline - time.time()) * 1000 if deadline else 120000
            if remaining <= 0:
                raise RuntimeError("Timeout before navigation to seller page")
            page.goto(seller_url, wait_until="domcontentloaded", timeout=min(remaining, 30000))
            page.wait_for_timeout(300)
            
        seller_path = extract_relative_url(seller_url)
        all_items: list[dict[str, Any]] = []
        seen_skus: set[str] = set()
        seen_paths: set[str] = set()
        pages_fetched = 0
        next_path: str | None = seller_path
        while next_path and next_path not in seen_paths and pages_fetched < 100:
            if deadline and time.time() > deadline:
                break # Partial results better than total failure
                
            seen_paths.add(next_path)
            raw = page.evaluate(
                """
                async ({ sellerPath, timeoutMs }) => {
                  const controller = new AbortController();
                  const timer = setTimeout(() => controller.abort(), timeoutMs);
                  try {
                    const response = await fetch(`/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(sellerPath)}`, {
                      credentials: "include",
                      signal: controller.signal
                    });
                    clearTimeout(timer);
                    return {
                      ok: response.ok,
                      status: response.status,
                      text: await response.text(),
                    };
                  } catch (e) {
                    return { ok: false, status: 0, text: String(e) };
                  }
                }
                """,
                {"sellerPath": next_path, "timeoutMs": 8000},
            )
            if not raw.get("ok"):
                # If API fails, we might want to break and try DOM fallback
                raise RuntimeError(f"seller home api request failed with HTTP {raw.get('status')}")
            
            import json
            data = json.loads(raw["text"])
            parsed = parse_seller_home_page(data)
            items = parsed.get("items") or []
            if not items and pages_fetched == 0:
                # If first page is empty via API, something is wrong
                raise RuntimeError("API returned empty items on first page")
                
            for item in items:
                sku = str(item.get("sku") or "")
                key = sku or str(item.get("href") or item.get("product_url") or "")
                if not key or key in seen_skus:
                    continue
                seen_skus.add(key)
                all_items.append(item)
            next_path = parsed.get("next_page")
            pages_fetched += 1
            
        return {
            "page_url": seller_url,
            "page_title": page.title(),
            "items": all_items,
            "next_page": next_path,
            "source": "entrypoint_api_full",
            "pages_fetched": pages_fetched,
        }

    def _fetch_seller_home_products_dom(self, page: Any, seller_url: str, max_scrolls: int = 8, deadline: float | None = None) -> dict[str, Any]:
        remaining = (deadline - time.time()) * 1000 if deadline else 120000
        if remaining <= 0:
            raise RuntimeError("Timeout before DOM navigation")
            
        page.goto(seller_url, wait_until="domcontentloaded", timeout=min(remaining, 120000))
        page.wait_for_timeout(1500)
        
        for _ in range(max_scrolls):
            if deadline and time.time() > deadline:
                break
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(600)
            
        raw = page.evaluate(
            """
            () => {
              const map = new Map();
              const products = Array.from(document.querySelectorAll('a[href*="/product/"]'));
              if (products.length === 0) return null;
              
              for (const a of products) {
                const href = a.href;
                if (!href) continue;
                const text = (a.textContent || '').trim();
                const card = a.closest('[class*="tile"], [class*="widget"], article, li, div');
                const img = card ? card.querySelector('img') : null;
                const priceText = card ? (card.textContent || '') : '';
                const current = map.get(href) || {
                  href,
                  title: '',
                  badge_texts: [],
                  image_url: img ? (img.currentSrc || img.src || null) : null,
                  raw_text: priceText.trim(),
                };
                if (text) {
                  if (text.length > current.title.length && !/^новинка$|^вау-цены$/i.test(text)) {
                    current.title = text;
                  } else if (/^новинка$|^вау-цены$/i.test(text)) {
                    current.badge_texts.push(text);
                  }
                }
                if (!current.image_url && img) {
                  current.image_url = img.currentSrc || img.src || null;
                }
                map.set(href, current);
              }
              return {
                page_url: location.href,
                page_title: document.title,
                items: Array.from(map.values()),
              };
            }
            """
        )
        if not raw:
            raise RuntimeError(f"DOM scroll failed to find any products on {seller_url}")
            
        raw["source"] = "dom_scroll"
        return raw

    def _fetch_top_list_page(self, page: Any, filters: dict[str, Any], page_no: int, page_size: int = 50) -> dict[str, Any]:
        self._ensure_maozi_selection_ready(page)
        return page.evaluate(
            """
            async ({ filters, pageNo, pageSize, timeoutMs }) => {
              const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
              const token = access.accessToken || '';
              if (!token) {
                throw new Error('maozierp-core-access.accessToken is missing');
              }
              const params = new URLSearchParams();
              for (const [key, value] of Object.entries(filters || {})) {
                if (Array.isArray(value)) {
                  for (const item of value) {
                    params.append(`${key}[]`, item ?? '');
                  }
                  continue;
                }
                params.set(key, value ?? '');
              }
              params.set('page', String(pageNo));
              params.set('page_size', String(pageSize));
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), timeoutMs);
              const response = await fetch(`https://api.maozierp.com/api.selection.top/lists?${params.toString()}`, {
                method: 'GET',
                credentials: 'include',
                signal: controller.signal,
                headers: {
                  'Accept': 'application/json, text/plain, */*',
                  'Authorization': `Bearer ${token}`,
                  'Client': 'pc',
                  'X-Client-Type': 'pc',
                  'DNT': '1'
                }
              });
              clearTimeout(timer);
              const text = await response.text();
              let data = null;
              try {
                data = JSON.parse(text);
              } catch (error) {
              }
              return {
                ok: response.ok,
                status: response.status,
                text,
                data
              };
            }
            """,
            {"filters": filters, "pageNo": int(page_no), "pageSize": int(page_size), "timeoutMs": 20000},
        )

    def _ensure_maozi_selection_ready(self, page: Any) -> None:
        if not page.url.startswith(MAOZI_SELECTION_ORIGIN):
            page.goto(MAOZI_SELECTION_URL, wait_until="domcontentloaded", timeout=120000)
        # 等待 SPA 渲染完成：页面标题稳定且有实质内容
        for _ in range(20):
            page.wait_for_timeout(500)
            body_text = str(
                page.evaluate("() => (document.body?.innerText || '')")
                or ""
            ).strip()
            if len(body_text) > 20:
                break
        challenge_text = str(
            page.evaluate(
                """
                () => {
                  const title = document.title || '';
                  const body = document.body?.innerText || '';
                  return `${title}\n${body}`.trim();
                }
                """
            )
            or ""
        ).lower()
        if any(marker.lower() in challenge_text for marker in MAOZI_CHALLENGE_MARKERS):
            print(f"WARN: Maozi website returned Cloudflare challenge, skipping verification")

    def _extension_popup_url(self) -> str:
        return f"chrome-extension://{self.extension_id}/popup.html"


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def resolve_optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return resolve_path(value)


def extract_relative_url(value: str) -> str:
    if not value:
        return "/"
    if value.startswith("http://") or value.startswith("https://"):
        match = re.match(r"^https?://[^/]+(?P<path>/.*)?$", value)
        if match:
            return match.group("path") or "/"
    return value if value.startswith("/") else f"/{value}"


def detect_chrome_executable() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/snap/bin/chromium"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_chrome_version(value: str) -> str | None:
    match = re.search(r"(?:Chrome|Chromium)[ /]([0-9]+(?:\.[0-9]+){1,3})", value or "")
    if match:
        return match.group(1)
    return None


def build_windows_chrome_user_agent(version: str) -> str:
    normalized = version or "136.0.0.0"
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{normalized} Safari/537.36"
    )


def import_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright Python package is not installed in the current interpreter. "
            "Run `python -m pip install -r requirements.txt` in the project directory first."
        ) from exc
    return sync_playwright


def parse_plugin_card_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next((index for index, line in enumerate(lines) if line.startswith("类目：")), -1)
    if start < 0:
        return {"metric_overrides": {}, "card_lines": []}

    stop_markers = {"一键上架", "编辑上架", "Купить сейчас"}
    card_lines: list[str] = []
    for line in lines[start:]:
        if line in stop_markers:
            break
        card_lines.append(line)

    line_map: dict[str, str] = {}
    index = 0
    while index < len(card_lines):
        line = card_lines[index]
        if line.startswith("rFBS佣金："):
            values = []
            for extra in card_lines[index + 1 : index + 4]:
                if "：" in extra:
                    break
                values.append(extra)
            line_map["rFBS佣金"] = "|".join(values)
            index += max(1, len(values) + 1)
            continue
        if "：" in line:
            label, value = line.split("：", 1)
            line_map[label.strip()] = value.strip()
        index += 1

    metric_overrides: dict[str, Any] = {}
    value = clean_card_value(line_map.get("类目"))
    if value is not None:
        metric_overrides["category"] = value

    value = clean_card_value(line_map.get("品牌"))
    if value is not None:
        metric_overrides["brand"] = value

    value = clean_card_value(line_map.get("月销量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["sold_count"] = parsed

    value = clean_card_value(line_map.get("月销售额"))
    if value is not None:
        metric_overrides["sold_sum_text"] = value

    value = clean_card_value(line_map.get("月周转动态"))
    parsed = to_decimal(value)
    if parsed is not None:
        metric_overrides["sales_dynamics"] = parsed

    value = clean_card_value(line_map.get("广告费占比"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["drr"] = parsed

    value = clean_card_value(line_map.get("参与促销天数"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["days_in_promo"] = parsed

    value = clean_card_value(line_map.get("参与促销的折扣"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["discount"] = parsed

    value = clean_card_value(line_map.get("促销活动的转化率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["promo_revenue_share"] = parsed

    value = clean_card_value(line_map.get("付费推广天数"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["days_with_trafarets"] = parsed

    value = clean_card_value(line_map.get("商品卡浏览量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["qty_view_pdp"] = parsed

    value = clean_card_value(line_map.get("商品卡加购率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_to_cart_pdp"] = parsed

    value = clean_card_value(line_map.get("搜索目录浏览量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["session_count_search"] = parsed

    value = clean_card_value(line_map.get("搜索目录加购率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_to_cart_search"] = parsed

    value = clean_card_value(line_map.get("展示转化率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_view_to_order"] = parsed

    value = clean_card_value(line_map.get("商品点击率"))
    if value is not None:
        metric_overrides["custom_click_rate_text"] = value
        parsed = percent_text_to_decimal(value)
        if parsed is not None:
            metric_overrides["custom_click_rate"] = parsed

    value = clean_card_value(line_map.get("发货模式"))
    if value is not None:
        metric_overrides["sales_schema"] = value

    value = clean_card_value(line_map.get("退货取消率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["nullable_redemption_rate"] = parsed

    value = clean_card_value(line_map.get("长 宽 高"))
    if value is not None:
        metric_overrides["custom_volume_text"] = value

    value = clean_card_value(line_map.get("重 量"))
    if value is not None:
        metric_overrides["custom_weight_text"] = value
        parsed = grams_from_text(value)
        if parsed is not None:
            metric_overrides["custom_weight_g"] = parsed

    value = clean_card_value(line_map.get("上架时间"))
    if value is not None:
        metric_overrides["nullable_create_date_text"] = value
        match = re.search(r"\((\d+)天\)", value)
        if match:
            metric_overrides["create_days"] = int(match.group(1))

    value = clean_card_value(line_map.get("跟卖列表"))
    seller_offer_count = None
    if value is not None:
        match = re.search(r"等(\d+)个卖家", value)
        if match:
            seller_offer_count = int(match.group(1))

    return {
        "metric_overrides": metric_overrides,
        "seller_offer_count": seller_offer_count,
        "card_lines": card_lines,
        "line_map": line_map,
    }


def clean_card_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"暂无数据", "--", "-", "无"}:
        return None
    return text
