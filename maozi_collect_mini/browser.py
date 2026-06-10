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
        self._ozon_page: Any = None  # 缓存ozon.ru页面用于跟卖API同源请求

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
        self._ozon_page = None

    def _close_page_safe(self, page: Any) -> None:
        """安全关闭页面，忽略所有异常"""
        try:
            page.close()
        except Exception:
            pass

    def _cleanup_excess_pages(self) -> int:
        """当页面数超过阈值时，关闭非核心页面释放内存。
        
        仅保留最近使用的 maozi_page 和 ozon_page，其余全部关闭。
        返回关闭的页面数。
        """
        if not self._context:
            return 0
        try:
            all_pages = list(self._context.pages)
        except Exception:
            return 0
        if len(all_pages) <= 10:
            return 0
        # 保留 maozi_page + ozon_page（如果存活），其余全部关闭
        keep = set()
        for p in (self._maozi_page, self._ozon_page):
            if p is None:
                continue
            try:
                p.url  # 探活
                keep.add(p)
            except Exception:
                pass
        closed = 0
        for page in all_pages:
            if page in keep:
                continue
            self._close_page_safe(page)
            closed += 1
        return closed

    def get_maozi_page(self) -> Any:
        """获取或创建毛子ERP选品页面"""
        if self._maozi_page:
            try:
                self._maozi_page.title()
                return self._maozi_page
            except Exception:
                self._close_page_safe(self._maozi_page)
                self._maozi_page = None

        self._find_maozi_page()
        if self._maozi_page:
            return self._maozi_page

        self._cleanup_excess_pages()
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

    def _get_ozon_page(self) -> Any:
        """获取或创建ozon.ru页面（用于同源请求跟卖API）"""
        if self._ozon_page:
            try:
                self._ozon_page.title()
                return self._ozon_page
            except Exception:
                self._close_page_safe(self._ozon_page)
                self._ozon_page = None
        self._cleanup_excess_pages()
        page = self._context.new_page()
        page.goto("https://www.ozon.ru", wait_until="domcontentloaded")
        self._ozon_page = page
        return page

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
        filters_json = json.dumps(filters, ensure_ascii=False)

        result = page.evaluate(
            f"""
                async () => {{
                    const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{{}}');
                    const token = access.accessToken || '';
                    if (!token) {{
                        return {{ok: false, error: 'TOKEN_MISSING'}};
                    }}
                    try {{
                        const filters = {filters_json};
                        const params = new URLSearchParams();
                        for (const [key, value] of Object.entries(filters || {{}})) {{
                            if (Array.isArray(value)) {{
                                for (const item of value) {{
                                    if (item != null && item !== '') {{
                                        params.append(key + '[]', String(item));
                                    }}
                                }}
                                continue;
                            }}
                            if (value != null && value !== '') {{
                                params.set(key, String(value));
                            }}
                        }}
                        params.set('page', String({page_no}));
                        params.set('page_size', String({page_size}));
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), 20000);
                        const r = await fetch('https://api.maozierp.com/api.selection.top/lists?' + params.toString(), {{
                            method: 'GET',
                            credentials: 'include',
                            signal: controller.signal,
                            headers: {{
                                'Accept': 'application/json, text/plain, */*',
                                'Authorization': 'Bearer ' + token,
                                'Client': 'pc',
                                'X-Client-Type': 'pc',
                                'DNT': '1'
                            }}
                        }});
                        clearTimeout(timer);
                        const text = await r.text();
                        let data = null;
                        try {{
                            data = JSON.parse(text);
                        }} catch(e) {{}}
                        return {{ok: r.ok, status: r.status, data: data, text: text}};
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
        self, seller_url: str, max_pages: int = 100, page_timeout: int = 120
    ) -> dict[str, Any]:
        """通过Ozon entrypoint API拉取卖家主页全部商品（翻到最后一页为止）。
        
        参数：
            seller_url: 卖家主页完整URL
            max_pages: 安全上限（默认100页≈2000商品，正常卖家不会超过）
            page_timeout: 单页超时秒数
        
        返回：
            {"items": [{href, sku, title, price_text, price_amount, currency, image_url}, ...],
             "source": "entrypoint_api", "pages_fetched": N}
        """
        try:
            # 从缓存ozon页面导航到卖家主页（同源请求，视觉反馈）
            page = self._get_ozon_page()
            remaining = page_timeout * 1000
            page.goto(seller_url, wait_until="domcontentloaded", timeout=remaining)
            page.wait_for_timeout(300)

            # 提取卖家路径（相对URL）
            from urllib.parse import urlparse
            parsed = urlparse(seller_url)
            seller_path = parsed.path or "/"
            if parsed.query:
                seller_path += "?" + parsed.query

            import json
            all_items: list[dict[str, Any]] = []
            seen_skus: set[str] = set()
            pages_fetched = 0
            next_path: str | None = seller_path

            while next_path and pages_fetched < max_pages:
                raw = page.evaluate(
                    """
                    async ({ sellerPath, timeoutMs }) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const response = await fetch(
                                `/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(sellerPath)}`,
                                { credentials: "include", signal: controller.signal }
                            );
                            clearTimeout(timer);
                            return { ok: response.ok, status: response.status, text: await response.text() };
                        } catch (e) {
                            return { ok: false, status: 0, text: String(e) };
                        }
                    }
                    """,
                    {"sellerPath": next_path, "timeoutMs": 8000},
                )

                if not raw.get("ok"):
                    if pages_fetched == 0:
                        return {"error": f"seller home api HTTP {raw.get('status')}", "items": []}
                    break

                data = json.loads(raw["text"])
                widget_states = data.get("widgetStates") or {}

                # 解析商品tiles（tileGridDesktop-* widget）
                grid_key = next((k for k in widget_states if k.startswith("tileGridDesktop-")), None)
                if grid_key:
                    grid_raw = widget_states[grid_key]
                    if isinstance(grid_raw, str):
                        grid_raw = json.loads(grid_raw)
                    tiles = grid_raw.get("items") or []
                    for tile in tiles:
                        action = tile.get("action") or {}
                        href = action.get("link")
                        if not href:
                            continue
                        sku = str(tile.get("sku") or tile.get("id") or "")
                        key = sku or href
                        if key in seen_skus:
                            continue
                        seen_skus.add(key)
                        # 提取title（来自mainState中的textAtom）
                        title = None
                        for block in tile.get("mainState") or []:
                            if block.get("type") == "textAtom":
                                title = ((block.get("textAtom") or {}).get("text") or "").strip() or None
                                break
                        # 提取price（来自mainState中的priceV2）
                        price_text = None
                        for block in tile.get("mainState") or []:
                            if block.get("type") != "priceV2":
                                continue
                            parts = ((block.get("priceV2") or {}).get("price") or [])
                            price_text = "".join((part.get("text") or "") for part in parts).strip() or None
                            break
                        # 提取图片
                        image_url = None
                        tile_image = tile.get("tileImage") or {}
                        for img_item in tile_image.get("items") or []:
                            img_link = (img_item.get("image") or {}).get("link")
                            if img_link:
                                image_url = img_link
                                break
                        # 提取角标（如 "Нет в наличии" 无库存标记）
                        badge_text = None
                        badge_v2 = tile_image.get("leftBottomBadgeV2")
                        if isinstance(badge_v2, dict):
                            badge_text = badge_v2.get("text")
                        # 提取库存上限（multiButton → addToCart → quantityButton.maxItems）
                        stock_max = None
                        multi_button = tile.get("multiButton") or {}
                        ozon_button = multi_button.get("ozonButton") or {}
                        atc = ozon_button.get("addToCart") or {}
                        qb = atc.get("quantityButton") or {}
                        max_items = qb.get("maxItems")
                        if isinstance(max_items, (int, float)):
                            stock_max = int(max_items)
                        # 提取品牌logo URL
                        brand_logo_url = None
                        brand_logo = tile.get("brandLogo")
                        if isinstance(brand_logo, dict):
                            logo = brand_logo.get("logo")
                            if logo:
                                brand_logo_url = logo
                        # 价格数值
                        price_amount = None
                        currency = None
                        if price_text:
                            import re as _re
                            cleaned = _re.sub(r"[^\d.,]", "", price_text.replace(",", "."))
                            try:
                                price_amount = float(cleaned)
                            except ValueError:
                                pass
                            if "₽" in price_text:
                                currency = "RUB"
                        all_items.append({
                            "href": href,
                            "product_url": href,
                            "sku": sku,
                            "title": title,
                            "price_text": price_text or "",
                            "price_amount": price_amount,
                            "currency": currency,
                            "image_url": image_url,
                            "stock_max": stock_max,
                            "badge": badge_text,
                            "brand_logo_url": brand_logo_url,
                        })

                # 翻页（infiniteVirtualPaginator-* widget中的nextPage）
                next_path = data.get("nextPage")
                if not next_path:
                    pag_key = next((k for k in widget_states if k.startswith("infiniteVirtualPaginator-")), None)
                    if pag_key:
                        pag_raw = widget_states[pag_key]
                        if isinstance(pag_raw, str):
                            pag_raw = json.loads(pag_raw)
                        if isinstance(pag_raw, dict):
                            next_path = pag_raw.get("nextPage")
                pages_fetched += 1
                time.sleep(0.3)  # 页面间延迟

            return {
                "page_url": seller_url,
                "page_title": page.title(),
                "items": all_items,
                "pages_fetched": pages_fetched,
                "source": "entrypoint_api",
            }

        except Exception as exc:
            return {"error": str(exc), "items": []}

    def fetch_seller_offers(self, sku: str) -> list[dict[str, Any]]:
        """获取SKU的跟卖卖家列表（Ozon开放API，从缓存ozon.ru页面同源请求）"""
        try:
            page = self._get_ozon_page()
            result = page.evaluate(
                f"""
                    async (sku) => {{
                        try {{
                            const target = `/modal/otherOffersFromSellers?product_id=${{sku}}`;
                            const url = `/api/entrypoint-api.bx/page/json/v2?url=${{encodeURIComponent(target)}}`;
                            const controller = new AbortController();
                            const timer = setTimeout(() => controller.abort(), 15000);
                            const r = await fetch(url, {{
                                credentials: 'include',
                                signal: controller.signal
                            }});
                            clearTimeout(timer);
                            if (!r.ok) {{
                                return [];
                            }}
                            const data = await r.json();
                            const ws = data?.widgetStates || {{}};
                            const key = Object.keys(ws).find(k => k.startsWith('webSellerList-'));
                            if (!key) return [];
                            let raw = ws[key];
                            if (typeof raw === 'string') raw = JSON.parse(raw);
                            const sellers = raw?.sellers || [];
                            return sellers.map(s => ({{
                                seller_id: String(s.link || '').split('/seller/')[1]?.split('/')[0] || '',
                                seller_name: s.name || '',
                                seller_home_url: (s.link && !s.link.startsWith('http') ? 'https://www.ozon.ru' + s.link : s.link) || '',
                                offer_sku: String(s.sku || ''),
                            }}));
                        }} catch(e) {{
                            return [];
                        }}
                    }}
                """,
                sku,
            )
            if isinstance(result, list):
                return result
        except Exception:
            pass
        return []

    # ============================================================
    # 类目页商品采集（Ozon entrypoint API）
    # ============================================================
    def fetch_category_page(
        self,
        slug: str,
        category_id: int,
        page: int = 1,
        price_range: str | None = None,
        sorting: str | None = None,
        page_timeout: int = 15,
    ) -> dict[str, Any]:
        """通过 Ozon entrypoint API 获取类目页商品列表（单页）。

        参数：
            slug: 类目 URL slug，如 "elektronika"
            category_id: Ozon 类目 ID，如 15500
            page: 页码（1=第一页）
            price_range: 价格筛选，如 "1.000;250.000"（RUB）
            sorting: 排序方式，如 "score", "new", "price"
            page_timeout: 单次请求超时秒数

        返回：
            {"items": [...], "page": N, "total_pages": N|None, "has_next": bool,
             "brand_filtered": N, "total_items": N}
            或 {"error": "..."}
        """
        from urllib.parse import urlencode
        try:
            page_obj = self._get_ozon_page()

            # 构建类目 path
            category_path = f"/category/{slug}-{category_id}/"
            params = []
            if page > 1:
                params.append(("page", str(page)))
            if sorting:
                params.append(("sorting", sorting))
            if price_range:
                params.append(("currency_price", price_range))

            query_string = urlencode(params)
            full_path = category_path
            if query_string:
                full_path += "?" + query_string

            # JS 注入：调用 entrypoint API
            import json
            raw = page_obj.evaluate(
                """
                async ({ path, timeoutMs }) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const r = await fetch(
                            `/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(path)}`,
                            { credentials: "include", signal: controller.signal }
                        );
                        clearTimeout(timer);
                        return { ok: r.ok, status: r.status, text: await r.text() };
                    } catch (e) {
                        return { ok: false, status: 0, text: String(e) };
                    }
                }
                """,
                {"path": full_path, "timeoutMs": page_timeout * 1000},
            )

            if not raw.get("ok"):
                return {"error": f"entrypoint HTTP {raw.get('status')}: {raw.get('text', '')[:200]}", "items": []}

            data = json.loads(raw["text"])
            widget_states = data.get("widgetStates") or {}

            # 解析商品 tiles
            grid_key = next((k for k in widget_states if k.startswith("tileGridDesktop-")), None)
            items = []
            if grid_key:
                grid_raw = widget_states[grid_key]
                if isinstance(grid_raw, str):
                    grid_raw = json.loads(grid_raw)
                tiles = grid_raw.get("items") or []
                for tile in tiles:
                    info = self._extract_tile_info(tile)
                    if info:
                        items.append(info)

            # 翻页信息
            has_next = False
            total_pages = None
            pag_key = next((k for k in widget_states if k.startswith("infiniteVirtualPaginator-")), None)
            if pag_key:
                pag_raw = widget_states[pag_key]
                if isinstance(pag_raw, str):
                    pag_raw = json.loads(pag_raw)
                if isinstance(pag_raw, dict):
                    has_next = bool(pag_raw.get("nextPage"))
                    # 尝试提取总页数
                    total_pages = pag_raw.get("totalPages") or pag_raw.get("pageCount")

            # 如果翻页 widget 没给总页数，从 shared 或 pageInfo 提取
            if not total_pages:
                shared = data.get("shared") or {}
                if isinstance(shared, dict):
                    total_pages = shared.get("totalPages") or shared.get("pageCount")

            return {
                "items": items,
                "page": page,
                "total_pages": total_pages,
                "has_next": has_next,
                "total_items": len(items),
                "category_path": full_path,
            }

        except Exception as exc:
            return {"error": str(exc), "items": []}

    def _extract_tile_info(self, tile: dict[str, Any]) -> dict[str, Any] | None:
        """从 tileGridDesktop 的单个商品 tile 中提取字段"""
        import json as _json
        import re as _re

        # 防御：tile 必须是 dict
        if not isinstance(tile, dict):
            return None

        action = tile.get("action") or {}
        if not isinstance(action, dict):
            action = {}
        href = action.get("link")
        if not href:
            return None

        sku = str(tile.get("sku") or tile.get("id") or "")

        # 标题：优先取有 id 的 textDS（真正标题），其次取无 id 的（库存等）
        title = None
        fallback_title = None
        main_state = tile.get("mainState") or []
        if not isinstance(main_state, list):
            main_state = []
        for block in main_state:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "textDS":
                txt = ((block.get("textDS") or {}).get("text") or "").strip() or None
                if block.get("id"):
                    # 有 id 的 textDS 是产品标题
                    if not title:
                        title = txt
                        break
                elif not fallback_title and txt:
                    fallback_title = txt
            elif block_type == "textAtom":
                txt = ((block.get("textAtom") or {}).get("text") or "").strip() or None
                if txt:
                    if not title:
                        title = txt
                        break
        if not title:
            title = fallback_title

        # 价格（只取首个价格=当前售价，忽略划线原价）
        price_text = None
        price_amount = None
        currency = None
        for block in main_state:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "priceV2":
                continue
            parts = ((block.get("priceV2") or {}).get("price") or [])
            if parts:
                first = parts[0]
                if isinstance(first, dict):
                    price_text = (first.get("text") or "").strip() or None
            if price_text:
                cleaned = _re.sub(r"[^\d.,]", "", price_text.replace(",", "."))
                try:
                    price_amount = float(cleaned)
                except ValueError:
                    pass
                if "₽" in price_text:
                    currency = "RUB"
            break

        # 品牌 Logo（主要品牌判定依据）
        brand_logo_url = None
        brand_logo = tile.get("brandLogo")
        if isinstance(brand_logo, dict):
            brand_logo_url = brand_logo.get("logo")

        # labelListV2 品牌名（仅当有 brandLogo 时才作为补充信息提取）
        label_brand = None
        if brand_logo_url:
            for block in main_state:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "labelListV2":
                    continue
                label_items = (block.get("labelListV2") or {}).get("items") or []
                for li in label_items:
                    if not isinstance(li, dict):
                        continue
                    if li.get("type") == "text":
                        txt = ((li.get("text") or {}).get("text") or "").strip()
                        # 排除评分数字、评价计数等（仅数字/货币字符）
                        if txt and not _re.match(r"^[\d.,₽$€¥ ]+$", txt) and "评价" not in txt and "отзыв" not in txt:
                            label_brand = txt
                            break
                if label_brand:
                    break

        # 图片
        image_url = None
        tile_image = tile.get("tileImage") or {}
        if isinstance(tile_image, dict):
            for img_item in tile_image.get("items") or []:
                if not isinstance(img_item, dict):
                    continue
                img_link = (img_item.get("image") or {}).get("link")
                if img_link:
                    image_url = img_link
                    break

        # 库存
        stock_max = None
        multi_button = tile.get("multiButton") or {}
        if isinstance(multi_button, dict):
            ozon_button = multi_button.get("ozonButton") or {}
            if isinstance(ozon_button, dict):
                atc = ozon_button.get("addToCart") or {}
                if isinstance(atc, dict):
                    qb = atc.get("quantityButton") or {}
                    if isinstance(qb, dict):
                        max_items = qb.get("maxItems")
                        if isinstance(max_items, (int, float)):
                            stock_max = int(max_items)

        # 评分
        rating = tile.get("rating") or {}
        rating_value = rating.get("value") if isinstance(rating, dict) else None
        rating_count = rating.get("count") if isinstance(rating, dict) else None

        # 品牌判定（品牌 Logo 或 labelListV2 品牌名）
        is_branded = bool(brand_logo_url) or bool(label_brand)

        return {
            "sku": sku,
            "href": href,
            "product_url": href,
            "title": title,
            "price_text": price_text or "",
            "price_amount": price_amount,
            "currency": currency,
            "image_url": image_url,
            "stock_max": stock_max,
            "brand_logo_url": brand_logo_url,
            "label_brand": label_brand,
            "is_branded": is_branded,
            "rating_value": rating_value,
            "rating_count": rating_count,
            "raw_tile": _json.dumps(tile, ensure_ascii=False),
        }

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
