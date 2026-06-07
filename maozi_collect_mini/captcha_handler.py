"""验证码与登录恢复模块。

功能：
1. 滑块验证码自动解决（SliderHandler + 拟人化拖动）
2. 自动重新登录（auto_login: 填写凭证 + 滑块 + 点击）
3. 插件弹窗处理（handle_plugin_login_popup）
4. 多级恢复（unified_login_recovery）
"""

from __future__ import annotations

import random
import time
import math
from typing import Any

from .config import settings

try:
    from playwright.sync_api import Page, BrowserContext
except ImportError:
    Page = Any  # type: ignore
    BrowserContext = Any  # type: ignore

MAOZI_AUTH_URL = "https://ozon.maozierp.com/#/auth/login"
MAOZI_SELECTION_ORIGIN = "https://ozon.maozierp.com"


# ============================================================
# 滑块验证码处理
# ============================================================

class SliderHandler:
    """滑块验证码处理：拟人化拖动 vben-spine 滑块"""

    def __init__(self, page: Page):
        self._page = page

    def solve(self, max_retries: int = 3) -> bool:
        """自动拖动滑块，最多重试 max_retries 次"""
        for attempt in range(max_retries):
            if self._already_verified():
                print(f"[滑块] 已经验证通过，跳过拖动")
                return True

            print(f"[滑块] 第 {attempt + 1}/{max_retries} 次尝试拖动...")

            track_info = self._get_track_info()
            if not track_info:
                print("[滑块] 未找到滑块轨道元素")
                time.sleep(3)
                continue

            success = self._human_like_drag(track_info)
            if success:
                time.sleep(1.5)
                if self._check_result(5):
                    print("[滑块] 验证成功")
                    return True
                print("[滑块] 拖动完成但验证未通过，等待重试...")
            else:
                print("[滑块] 拖动执行失败")

            if attempt < max_retries - 1:
                time.sleep(2)

        return False

    def _already_verified(self) -> bool:
        """检查是否已经验证通过（滑块元素不存在或显示"验证通过"）"""
        try:
            text = self._page.evaluate("""() => {
                const el = document.querySelector('.vben-spine-text');
                return el ? el.innerText.trim() : null;
            }""")
            if text == "验证通过":
                return True
            return False
        except Exception:
            return False

    def _get_track_info(self) -> dict[str, Any] | None:
        """获取滑块轨道尺寸和滑块位置"""
        try:
            info = self._page.evaluate("""() => {
                const spineText = document.querySelector('.vben-spine-text');
                if (!spineText) return null;
                const spine = spineText.parentElement;
                if (!spine) return null;
                const spineRect = spine.getBoundingClientRect();
                const block = spine.querySelector('.vben-spine-block') || spine;
                const blockRect = block.getBoundingClientRect();
                return {
                    trackLeft: spineRect.left,
                    trackTop: spineRect.top,
                    trackWidth: spineRect.width,
                    trackHeight: spineRect.height,
                    blockWidth: blockRect.width,
                    blockHeight: blockRect.height,
                    blockLeft: blockRect.left,
                    blockTop: blockRect.top,
                };
            }""")
            if not info:
                return None
            info["startX"] = info["blockLeft"] + info["blockWidth"] / 2
            info["startY"] = info["blockTop"] + info["blockHeight"] / 2
            info["targetX"] = info["trackLeft"] + info["trackWidth"] - info["blockWidth"] / 2
            return info
        except Exception:
            return None

    def _human_like_drag(self, track_info: dict[str, Any]) -> bool:
        """拟人化拖动滑块：慢-快-慢缓动 + 随机抖动"""
        try:
            self._page.mouse.move(track_info["startX"], track_info["startY"])
            time.sleep(random.uniform(0.1, 0.2))
            self._page.mouse.down()
            time.sleep(random.uniform(0.05, 0.1))

            total_distance = track_info["targetX"] - track_info["startX"]
            steps = random.randint(40, 60)
            base_y = track_info["startY"]

            for i in range(steps):
                progress = i / (steps - 1)
                # 缓动曲线: 慢-快-慢 (ease-in-out)
                eased = _ease_in_out(progress)
                target_x = track_info["startX"] + total_distance * eased

                # 加一点随机过冲（模拟惯性）
                if progress > 0.9:
                    overshoot = total_distance * random.uniform(0.0, 0.03)
                    target_x += overshoot

                y_jitter = random.gauss(0, 1.5)  # 高斯抖动
                target_y = base_y + y_jitter

                self._page.mouse.move(target_x, target_y)
                # 不均匀时间间隔，模拟人类操作
                time.sleep(random.uniform(0.003, 0.015))

            self._page.mouse.up()
            return True
        except Exception:
            return False

    def _check_result(self, timeout_seconds: int = 5) -> bool:
        """轮询检查验证结果"""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                text = self._page.evaluate("""() => {
                    const el = document.querySelector('.vben-spine-text');
                    return el ? el.innerText.trim() : null;
                }""")
                if text == "验证通过":
                    return True
                # 滑块元素消失也是通过
                if text is None:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False


def _ease_in_out(t: float) -> float:
    """慢-快-慢缓动函数"""
    if t < 0.5:
        return 2 * t * t
    return -1 + (4 - 2 * t) * t


# ============================================================
# 页面状态检测
# ============================================================

def detect_captcha_present(page: Page) -> bool:
    """检测页面是否出现滑块验证码"""
    try:
        has_spine = page.evaluate("""() => {
            const spine = document.querySelector('.vben-spine-text');
            return !!spine;
        }""")
        return bool(has_spine)
    except Exception:
        return False


def detect_login_expired(page: Page) -> bool:
    """检测页面是否跳转到登录页"""
    try:
        url = page.url
        return MAOZI_AUTH_URL in url.lower()
    except Exception:
        return False


# ============================================================
# 自动登录
# ============================================================

def auto_login(context: BrowserContext, max_retries: int = 3) -> bool:
    """自动完成毛子ERP登录流程（含滑块验证）

    流程：
    1. 打开登录页
    2. 填写用户名/密码
    3. 勾选"记住账号"
    4. 拖动滑块
    5. 点击登录按钮
    6. 等待跳转或 Token 出现
    """
    username = settings.maozi_username
    password = settings.maozi_password

    if not username or not password:
        print("[登录] MAOZI_USERNAME 或 MAOZI_PASSWORD 未配置，无法自动登录")
        return False

    for attempt in range(max_retries):
        print(f"[登录] 第 {attempt + 1}/{max_retries} 次尝试...")

        # 查找或创建登录页
        login_page = _find_or_create_page(context, MAOZI_AUTH_URL)
        if not login_page:
            print("[登录] 无法打开登录页")
            return False

        try:
            _navigate_to_login(login_page)
            time.sleep(3)

            # 等待 SPA 渲染（轮询 bodyText 含"请按住滑块拖动"）
            if not _wait_for_login_form(login_page, timeout=15):
                print("[登录] 登录表单未出现")
                continue

            time.sleep(1)

            # 填写表单
            _fill_login_form(login_page, username, password)

            # 勾选"记住账号"
            _check_remember(login_page)

            # 滑块验证
            slider = SliderHandler(login_page)
            if not slider.solve(max_retries=2):
                print("[登录] 滑块验证失败")
                continue

            # 点击登录按钮
            _click_login_button(login_page)
            time.sleep(2)

            # 等待登录成功
            if _wait_for_login_success(login_page, context, timeout=15):
                print("[登录] 登录成功")
                return True
            else:
                print("[登录] 未检测到登录成功信号")

        except Exception as exc:
            print(f"[登录] 异常: {exc}")
            continue

    print("[登录] 所有重试均失败")
    return False


def _find_or_create_page(context: BrowserContext, url_hint: str) -> Page | None:
    """在上下文中查找匹配URL的页面，找不到则新建"""
    for page in context.pages:
        try:
            if "maozierp.com" in page.url:
                return page
        except Exception:
            continue
    try:
        return context.new_page()
    except Exception as exc:
        print(f"[登录] 创建新页面失败: {exc}")
        return None


def _navigate_to_login(page: Page) -> None:
    """导航到登录页"""
    try:
        page.goto(MAOZI_AUTH_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass


def _wait_for_login_form(page: Page, timeout: int = 15) -> bool:
    """等待登录表单渲染（检测"请按住滑块拖动"文字）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.evaluate("() => document.body.innerText")
            if body and "请按住滑块拖动" in body:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _fill_login_form(page: Page, username: str, password: str) -> None:
    """填写登录表单"""
    try:
        # 清空并填写用户名
        inputs = page.query_selector_all("input")
        if len(inputs) >= 2:
            inputs[0].fill(username)
            time.sleep(0.3)
            inputs[1].fill(password)
            time.sleep(0.3)
    except Exception as exc:
        print(f"[登录] 填写表单失败: {exc}")


def _check_remember(page: Page) -> None:
    """勾选"记住账号"复选框"""
    try:
        page.evaluate("""() => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {
                if (!cb.checked) {
                    cb.click();
                }
            }
        }""")
        time.sleep(0.2)
    except Exception:
        pass


def _click_login_button(page: Page) -> None:
    """点击登录按钮"""
    try:
        page.evaluate("""() => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = (btn.innerText || '').trim();
                if (text.includes('登录') || text.includes('登 录')) {
                    btn.click();
                    return;
                }
            }
        }""")
    except Exception:
        pass


def _wait_for_login_success(page: Page, context: BrowserContext, timeout: int = 15) -> bool:
    """轮询等待登录成功：页面跳离登录页或 localStorage 出现 Token"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 检查当前页面是否已离开登录页
            if MAOZI_AUTH_URL not in page.url.lower():
                return True
        except Exception:
            pass

        # 检查任意毛子页面的 Token
        for pg in context.pages:
            try:
                if "maozierp.com" in pg.url.lower():
                    token = pg.evaluate("""() => {
                        const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                        return !!access.accessToken;
                    }""")
                    if token:
                        return True
            except Exception:
                continue

        time.sleep(1)

    return False


# ============================================================
# 插件弹窗处理
# ============================================================

def handle_plugin_login_popup(context: BrowserContext, max_retries: int = 2) -> bool:
    """处理毛子插件右下角"请登录"弹窗

    流程：
    1. 在所有页面中检测 MAOZIERP-UI shadow DOM 中的"请登录"按钮
    2. 点击按钮
    3. 监测是否打开新登录页 → 调用 auto_login
    4. 无新页面 → 检查弹窗是否消失视为成功
    """
    for attempt in range(max_retries):
        print(f"[弹窗] 第 {attempt + 1}/{max_retries} 次处理...")

        target_page = _find_plugin_popup_page(context)
        if not target_page:
            print("[弹窗] 未检测到"请登录"按钮")
            return False

        login_button_uid = _get_popup_button_uid(target_page)
        if not login_button_uid:
            return False

        before_pages = set(context.pages)

        # 点击"请登录"按钮
        _click_popup_button(target_page)
        time.sleep(3)

        # 检测是否有新登录页
        new_pages = [p for p in context.pages if p not in before_pages]
        login_page = None
        for p in new_pages:
            try:
                if MAOZI_AUTH_URL in p.url.lower():
                    login_page = p
                    break
            except Exception:
                continue

        if login_page:
            print("[弹窗] 检测到新登录页，执行自动登录...")
            success = auto_login(context)
            if success:
                try:
                    login_page.close()
                except Exception:
                    pass
            return success

        # 等待弹窗消失
        time.sleep(2)
        if not _get_popup_button_uid(target_page):
            print("[弹窗] 弹窗已消失，视为登录成功")
            return True

        time.sleep(2)

    return False


def _find_plugin_popup_page(context: BrowserContext) -> Page | None:
    """在所有页面中查找有"请登录"按钮的页面"""
    for page in context.pages:
        try:
            url = page.url.lower() if hasattr(page, 'url') else ''
            if "ozon.ru" not in url:
                continue
            buttons = page.evaluate("""() => {
                const host = document.querySelector('MAOZIERP-UI');
                if (!host || !host.shadowRoot) return false;
                for (const btn of host.shadowRoot.querySelectorAll('button')) {
                    if ((btn.innerText || '').trim() === '请登录') return true;
                }
                return false;
            }""")
            if buttons:
                return page
        except Exception:
            continue
    return None


def _get_popup_button_uid(page: Page) -> str | None:
    """检查指定页面是否有"请登录"按钮"""
    try:
        exists = page.evaluate("""() => {
            const host = document.querySelector('MAOZIERP-UI');
            if (!host || !host.shadowRoot) return false;
            for (const btn of host.shadowRoot.querySelectorAll('button')) {
                if ((btn.innerText || '').trim() === '请登录') return true;
            }
            return false;
        }""")
        return "exists" if exists else None
    except Exception:
        return None


def _click_popup_button(page: Page) -> None:
    """点击插件中的"请登录"按钮"""
    try:
        page.evaluate("""() => {
            const host = document.querySelector('MAOZIERP-UI');
            if (!host || !host.shadowRoot) return;
            for (const btn of host.shadowRoot.querySelectorAll('button')) {
                if ((btn.innerText || '').trim() === '请登录') {
                    btn.click();
                    return;
                }
            }
        }""")
    except Exception:
        pass


# ============================================================
# 多级统一恢复
# ============================================================

def unified_login_recovery(context: BrowserContext, browser_client=None) -> bool:
    """统一登录恢复入口，按优先级依次尝试：

    方法1: 刷新 Ozon 页面 → 检测插件"请登录"弹窗 → 点击处理
    方法2: 扫描所有毛子页面 → 如果登录页则 auto_login
    方法3: 主动打开登录页 → auto_login
    全部失败 → 飞书告警 + 返回 False
    """
    print("[恢复] 开始多级登录恢复...")

    # 方法1: 插件弹窗处理
    print("[恢复] 方法1: 处理插件弹窗...")
    if handle_plugin_login_popup(context):
        print("[恢复] 方法1 成功")
        return True

    # 方法2: 扫描现有页面
    print("[恢复] 方法2: 扫描现有页面...")
    for page in context.pages:
        try:
            if detect_login_expired(page):
                print("[恢复] 检测到登录页，执行自动登录...")
                if auto_login(context):
                    print("[恢复] 方法2 成功")
                    return True
            if detect_captcha_present(page):
                print("[恢复] 检测到滑块验证码...")
                slider = SliderHandler(page)
                if slider.solve():
                    if _check_token_after_recovery(context):
                        print("[恢复] 滑块验证后 Token 可用")
                        return True
        except Exception:
            continue

    # 方法3: 主动打开登录页
    print("[恢复] 方法3: 主动登录...")
    if auto_login(context):
        print("[恢复] 方法3 成功")
        return True

    # 全部失败 → 飞书告警
    print("[恢复] 所有恢复方法均失败!")
    _send_failure_alert(context)
    return False


def _check_token_after_recovery(context: BrowserContext) -> bool:
    """检查恢复后是否有有效的 Token"""
    for page in context.pages:
        try:
            url = page.url.lower() if hasattr(page, 'url') else ''
            if "maozierp.com" not in url:
                continue
            has_token = page.evaluate("""() => {
                const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                return !!access.accessToken;
            }""")
            if has_token:
                return True
        except Exception:
            continue
    return False


def _send_failure_alert(context: BrowserContext) -> None:
    """发送飞书失败告警"""
    try:
        from .feishu import send_error_notification
        page_urls = []
        for page in context.pages:
            try:
                page_urls.append(page.url[:100])
            except Exception:
                pass
        detail = "登录恢复失败\n"
        detail += f"当前页面: {', '.join(page_urls[:5])}"
        send_error_notification(detail)
    except Exception:
        pass
