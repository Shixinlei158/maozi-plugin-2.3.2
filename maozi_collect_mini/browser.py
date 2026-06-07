"""浏览器自动化模块 —— 精简版。

核心功能：
1. CDP连接（连接用户手动启动的Chrome）或独立启动
2. 榜单页面API调用（通过毛子ERP页面注入JS）
3. 卖家主页产品抓取
4. 登录态检测与恢复（处理"请登录"按钮）
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT_DIR, settings

MAOZI_SELECTION_URL = "https://ozon.maozierp.com/#/selection/top-list"
MAOZI_SELECTION_ORIGIN = "https://ozon.maozierp.com"
OZON_BASE = "https://www.ozon.ru"

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
    from playwright._impl._errors import TargetClosedError
except ImportError:
    TargetClosedError = Exception  # type: ignore


class BrowserClient:
    """简化的浏览器客户端，支持CDP连接和独立启动。"""

    def __init__(
        self,
        headless: bool | None = None,
    ) -> None:
        self.profile_dir = ROOT_DIR / settings.chrome_profile_dir
        self.extension_dir = Path(settings.chrome_extension_dir)
        self.extension_id = settings.chrome_extension_id
        self.executable_path = (
            Path(settings.chrome_executable_path)
            if settings.chrome_executable_path
            else None
        )
        self.channel = settings.chrome_channel
        self.cdp_url = settings.chrome_cdp_url or None
        self.remote_debugging_port = settings.chrome_remote_debugging_port
        self.headless = settings.chrome_headless if headless is None else headless

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._maozi_page: Any = None

    def open_session(self) -> None:
        """打开浏览器会话"""
        pw = sync_playwright()
        self._playwright = pw.start()

        if self.cdp_url:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = self._browser.new_context()
        else:
            launch_args: dict[str, Any] = {"headless": self.headless}
            if self.executable_path and self.executable_path.exists():
                launch_args["executable_path"] = str(self.executable_path)
            elif self.channel:
                launch_args["channel"] = self.channel
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir), **launch_args
            )
            self._browser = self._context.browser

        self._find_maozi_page()

    def close_session(self) -> None:
        """关闭会话（不关闭浏览器，不调用browser.close()）"""
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._maozi_page = None

    def get_maozi_page(self) -> Any:
        """获取或创建毛子ERP选品页面"""
        if self._maozi_page:
            try:
                self._maozi_page.title()
                return self._maozi_page
            except Exception:
                self._maozi_page = None

        self._find_maozi_page()
        if self._maozi_page:
            return self._maozi_page

        page = self._context.new_page()
        page.goto(MAOZI_SELECTION_URL, wait_until="domcontentloaded")
        self._maozi_page = page
        return page

    def _find_maozi_page(self) -> None:
        """在已有的页面中查找毛子页面"""
        if not self._context:
            return
        for page in self._context.pages:
            try:
                url = page.url
                if MAOZI_SELECTION_ORIGIN in url:
                    self._maozi_page = page
                    return
            except Exception:
                continue

    # ============================================================
    # 登录态检测与恢复
    # ============================================================
    def check_login_status(self) -> str:
        """检查毛子ERP登录状态。返回 'LOGGED_IN' | 'NEED_LOGIN' | 'NO_PLUGIN' | 'ERROR'"""
        page = self.get_maozi_page()
        try:
            result = page.evaluate("""
                () => {
                    const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                    const hasToken = !!access.accessToken;
                    const host = document.querySelector('MAOZIERP-UI');
                    if (!host || !host.shadowRoot) return hasToken ? 'LOGGED_IN' : 'NO_PLUGIN';
                    for (const btn of host.shadowRoot.querySelectorAll('button')) {
                        if (btn.innerText?.trim() === '请登录') return 'NEED_LOGIN';
                    }
                    return hasToken ? 'LOGGED_IN' : 'NO_PLUGIN';
                }
            """)
            return result
        except Exception:
            return "ERROR"

    def ensure_authenticated(self, max_retries: int = 3) -> bool:
        """确保已登录状态。集成多级恢复（滑块验证 + 自动登录 + 插件弹窗）。

        先快速检查，快速通过；失败后调用 captcha_handler.unified_login_recovery
        执行完整的恢复流程，全部失败则发送飞书告警。
        """
        # 快速路径: 已有 Token 且无"请登录"按钮
        status = self.check_login_status()
        if status == "LOGGED_IN":
            return True
        if status == "NEED_LOGIN" and max_retries <= 1:
            # 轻度恢复：只点击一次"请登录"按钮
            self._click_login_button_simple()
            time.sleep(3)
            return self.check_login_status() == "LOGGED_IN"

        # 重度恢复：多级恢复流程
        if not self._context:
            return False
        from .captcha_handler import unified_login_recovery
        return unified_login_recovery(self._context, browser_client=self)

    def _click_login_button_simple(self) -> None:
        """点击毛子插件中的"请登录"按钮（单次简单操作）"""
        page = self.get_maozi_page()
        page.evaluate("""
            () => {
                const host = document.querySelector('MAOZIERP-UI');
                if (!host || !host.shadowRoot) return;
                for (const btn of host.shadowRoot.querySelectorAll('button')) {
                    if (btn.innerText?.trim() === '请登录') {
                        btn.click();
                        return;
                    }
                }
            }
        """)

    def get_maozi_token_from_page(self) -> str:
        """从毛子页面localStorage获取token"""
        page = self.get_maozi_page()
        token = page.evaluate("""
            () => {
                const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                return access.accessToken || '';
            }
        """)
        return token

    # ============================================================
    # 榜单页面数据抓取
    # ============================================================
    def fetch_top_list_page(
        self, filters: dict[str, Any], page_no: int, page_size: int = 50
    ) -> dict[str, Any]:
        """在毛子页面中调用榜单API，获取一页数据"""
        page = self.get_maozi_page()
        api_params = dict(filters)
        api_params["page"] = page_no
        api_params["page_size"] = page_size

        json_str = json.dumps(api_params, ensure_ascii=False)

        result = page.evaluate(
            f"""
                async () => {{
                    const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{{}}');
                    const token = access.accessToken || '';
                    try {{
                        const r = await fetch('https://api.maozierp.com/api.selection.top/lists?{json_str}', {{
                            headers: {{
                                'Authorization': `Bearer ${{token}}`,
                                'Client': 'pc',
                                'X-Client-Type': 'pc'
                            }}
                        }});
                        const data = await r.json();
                        return {{ok: r.ok, status: r.status, data: data}};
                    }} catch(e) {{
                        return {{ok: false, error: 'FETCH_ERROR: ' + e.message}};
                    }}
                }}
            """,
        )
        return result

    # ============================================================
    # SKU3 批量调用（在毛子页面中）
    # ============================================================
    def fetch_sku3_batch(
        self, skus: list[str], concurrency: int = 5, chunk_delay_ms: int = 500
    ) -> dict[str, dict[str, Any]]:
        """在毛子页面中批量调用SKU3 API"""
        if not skus:
            return {}

        page = self.get_maozi_page()
        skus_json = json.dumps(skus, ensure_ascii=False)

        result = page.evaluate(
            f"""
                async () => {{
                    const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{{}}');
                    const token = access.accessToken || '';
                    const skus = {skus_json};
                    const results = {{}};
                    const concurrency = {concurrency};
                    const delay = {chunk_delay_ms};

                    const fetchOne = async (sku) => {{
                        try {{
                            const r = await fetch('https://api.maozierp.com/api.chrome/sku3', {{
                                method: 'POST',
                                credentials: 'include',
                                headers: {{
                                    'Authorization': `Bearer ${{token}}`,
                                    'Client': 'plugin',
                                    'Content-Type': 'application/json'
                                }},
                                body: JSON.stringify({{sku: sku}})
                            }});
                            const text = await r.text();
                            try {{
                                return JSON.parse(text);
                            }} catch(e) {{
                                return {{_error: 'PARSE_ERROR: ' + text.slice(0, 200)}};
                            }}
                        }} catch(e) {{
                            return {{_error: 'FETCH_ERROR: ' + e.message}};
                        }}
                    }};

                    for (let i = 0; i < skus.length; i += concurrency) {{
                        const batch = skus.slice(i, i + concurrency);
                        const promises = batch.map(sku =>
                            fetchOne(sku).then(r => ({{sku, result: r}}))
                        );
                        const batchResults = await Promise.all(promises);
                        for (const {{sku, result}} of batchResults) {{
                            results[sku] = result;
                        }}
                        if (i + concurrency < skus.length && delay > 0) {{
                            await new Promise(r => setTimeout(r, delay));
                        }}
                    }}
                    return results;
                }}
            """,
        )
        return result

    # ============================================================
    # 卖家主页产品抓取
    # ============================================================
    def fetch_seller_home_products(
        self, seller_url: str, max_scrolls: int = 8, page_timeout: int = 120
    ) -> dict[str, Any]:
        """打开卖家主页，滚动加载并抓取商品列表"""
        page = self._context.new_page() if self._context else None
        if not page:
            return {"error": "no_browser_context"}

        try:
            page.goto(seller_url, wait_until="domcontentloaded", timeout=page_timeout * 1000)
            time.sleep(3)

            items = []
            for scroll in range(max_scrolls):
                current_items = page.evaluate("""
                    () => {
                        const tiles = document.querySelectorAll('[data-widget="searchResultsV2"], '
                            + 'div[class*="tile"], div[class*="widget-search-result"]');
                        const results = [];
                        tiles.forEach(tile => {
                            const link = tile.querySelector('a[href*="/product/"]');
                            const img = tile.querySelector('img');
                            const priceEl = tile.querySelector('[class*="price"], span');
                            results.push({
                                href: link ? link.getAttribute('href') : null,
                                title: link ? link.textContent?.trim() : null,
                                image_url: img ? img.getAttribute('src') : null,
                                price_text: priceEl ? priceEl.textContent?.trim() : null,
                            });
                        });
                        return results;
                    }
                """)
                for item in current_items:
                    if item.get("href") and item["href"] not in {i["href"] for i in items}:
                        items.append(item)

                if scroll >= max_scrolls - 1:
                    break

                page.evaluate("window.scrollBy(0, window.innerHeight)")
                time.sleep(2)

            return {"items": items, "source": "seller_home"}

        except Exception as exc:
            return {"error": str(exc), "items": []}
        finally:
            try:
                page.close()
            except Exception:
                pass

    def fetch_seller_offers(self, sku: str) -> list[dict[str, Any]]:
        """获取SKU的跟卖卖家列表"""
        page = self.get_maozi_page()
        result = page.evaluate(
            f"""
                async () => {{
                    const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{{}}');
                    const token = access.accessToken || '';
                    try {{
                        const r = await fetch('https://api.maozierp.com/api.chrome/sellerOffers', {{
                            method: 'POST',
                            headers: {{
                                'Authorization': `Bearer ${{token}}`,
                                'Client': 'plugin',
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{sku: '{sku}'}})
                        }});
                        return await r.json();
                    }} catch(e) {{
                        return [];
                    }}
                }}
            """,
        )
        if isinstance(result, dict) and result.get("data"):
            return result["data"]
        return []

    def ping_cdp(self) -> dict[str, Any]:
        """检测CDP连接状态"""
        if not self.cdp_url:
            return {"reachable": False, "reason": "cdp_not_configured"}
        try:
            import requests
            r = requests.get(f"{self.cdp_url}/json/version", timeout=3)
            if r.status_code == 200:
                data = r.json()
                return {
                    "reachable": True,
                    "browser": data.get("Browser", "Unknown"),
                }
            return {"reachable": False}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)[:200]}

    def launch_real_chrome(self) -> None:
        """启动真实Chrome浏览器（CDP模式）"""
        import subprocess

        chrome_exe = self._find_chrome_exe()

        profile = self.profile_dir
        profile.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(chrome_exe),
            f"--remote-debugging-port={self.remote_debugging_port}",
            f"--user-data-dir={str(profile)}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            MAOZI_SELECTION_URL,
        ]
        subprocess.Popen(cmd)

    @staticmethod
    def _find_chrome_exe() -> Path:
        import shutil
        # 1. 先检查环境变量配置
        from .config import settings
        custom = settings.chrome_executable_path
        if custom:
            p = Path(custom)
            if p.exists():
                return p
        # 2. 检查常见安装路径
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
        ]
        for p in candidates:
            if p.exists():
                return p
        # 3. 希望在PATH中能找到
        found = shutil.which("chrome")
        if found:
            return Path(found)
        found = shutil.which("chrome.exe")
        if found:
            return Path(found)
        raise FileNotFoundError("无法找到 Chrome 浏览器，请在 settings.json 中设置 CHROME_EXECUTABLE_PATH")
