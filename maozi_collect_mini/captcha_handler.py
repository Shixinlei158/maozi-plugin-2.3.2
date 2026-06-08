"""验证码与登录恢复模块。

功能：
1. 滑块验证码自动解决（SliderHandler + 拟人化拖动）
2. 自动重新登录（auto_login: 填写凭证 + 滑块 + 点击）
3. 插件弹窗处理（handle_plugin_login_popup）
4. 多级恢复（unified_login_recovery）
"""

from __future__ import annotations

import random
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
    """拟人化滑块拖动处理器

    针对毛子ERP登录页面的vben-spine滑块验证：
    - 查找 .vben-spine-text 元素
    - 从其父级的父级获取轨道尺寸
    - 从轨道左边缘拟人化拖动到右边缘
    """

    def __init__(self, page: Page):
        self.page = page

    def solve(self, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                if self._already_verified():
                    print("[滑块] 已经验证通过")
                    return True

                track = self._get_track_info()
                if not track:
                    print("[滑块] 未找到滑块轨道")
                    return False

                start_x = track['x'] + 20
                start_y = track['y'] + track['h'] / 2
                end_x = track['x'] + track['w'] - 20

                print(f"[滑块] 拖动: ({start_x:.0f},{start_y:.0f}) -> ({end_x:.0f},{start_y:.0f}) 距离={end_x-start_x:.0f}px")

                self._human_like_drag(start_x, start_y, end_x)

                self.page.wait_for_timeout(800)
                if self._check_result():
                    print("[滑块] 验证成功")
                    return True

                print(f"[滑块] 拖动完成但验证未通过，等待重试...")
                self.page.wait_for_timeout(random.uniform(500, 1500))

            except Exception as exc:
                print(f"[滑块] 处理失败 (尝试 {attempt+1}): {exc}")
                try:
                    self.page.mouse.up()
                except Exception:
                    pass
                self.page.wait_for_timeout(1000)

        return False

    def _already_verified(self) -> bool:
        try:
            text = self.page.evaluate("""() => {
                const s = document.querySelector('.vben-spine-text');
                if (!s) return '__ELEMENT_GONE__';
                return s.innerText || '';
            }""")
            return text == '验证通过' or text == '__ELEMENT_GONE__'
        except Exception:
            return False

    def _get_track_info(self) -> dict[str, float] | None:
        """获取轨道尺寸：从 spine 元素往上两级取轨道"""
        try:
            info = self.page.evaluate("""() => {
                const spine = document.querySelector('.vben-spine-text');
                if (!spine) return null;
                const track = spine.parentElement.parentElement;
                const rect = track.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return null;
                return {x: rect.x, y: rect.y, w: rect.width, h: rect.height};
            }""")
            return info
        except Exception:
            return None

    def _human_like_drag(self, start_x: float, start_y: float, end_x: float) -> None:
        distance = end_x - start_x

        self.page.mouse.move(start_x, start_y)
        self.page.wait_for_timeout(random.uniform(100, 200))

        self.page.mouse.down()
        self.page.wait_for_timeout(random.uniform(50, 100))

        steps = random.randint(40, 60)
        for i in range(1, steps + 1):
            progress = i / steps

            # 三段分段缓动：慢-快-慢（与原版对齐）
            if progress < 0.15:
                eased = progress * 0.6
            elif progress < 0.85:
                eased = 0.09 + (progress - 0.15) * 1.1
            else:
                eased = 0.86 + (progress - 0.85) * 0.93
            eased = min(eased, 1.0)

            cx = start_x + distance * eased
            cy = start_y + random.gauss(0, 1.5)

            dt = 0.015 + random.uniform(0, 0.015)
            if progress > 0.9:
                dt += random.uniform(0.01, 0.03)

            self.page.mouse.move(cx, cy)
            self.page.wait_for_timeout(int(dt * 1000))

        # 最终对齐
        self.page.mouse.move(end_x, start_y)
        self.page.wait_for_timeout(random.uniform(30, 80))

        self.page.mouse.up()

    def _check_result(self) -> bool:
        """轮询检查验证结果（8次 × 0.5秒 = 4秒，与原版对齐）"""
        try:
            for _ in range(8):
                self.page.wait_for_timeout(500)
                text = self.page.evaluate("""() => {
                    const s = document.querySelector('.vben-spine-text');
                    if (!s) return '__ELEMENT_GONE__';
                    return s.innerText || '';
                }""")
                if text == '验证通过':
                    return True
                if text == '__ELEMENT_GONE__':
                    return True
            return False
        except Exception:
            return False


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

def auto_login(
    page: Page,
    username: str | None = None,
    password: str | None = None,
    max_retries: int = 3,
) -> bool:
    """自动完成毛子ERP登录流程（含滑块验证）

    流程：
    1. 导航到登录页
    2. 等待SPA渲染
    3. 检查是否需要填账号密码（可能已预填）
    4. 拖动滑块完成验证
    5. 点击登录按钮
    6. 等待页面跳转
    """
    if username is None:
        username = settings.maozi_username
    if password is None:
        password = settings.maozi_password

    if not username or not password:
        print("[登录] MAOZI_USERNAME 或 MAOZI_PASSWORD 未配置，无法自动登录")
        return False

    for attempt in range(max_retries):
        print(f"[登录] 第 {attempt + 1}/{max_retries} 次尝试...")

        try:
            page.goto(MAOZI_AUTH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 等待SPA渲染
            for _ in range(30):
                body_text = page.evaluate("() => document.body?.innerText || ''")
                if '请按住滑块拖动' in body_text or '请登录' in body_text:
                    break
                page.wait_for_timeout(300)

            # 填写账号密码（使用 input[name] 精确选择器）
            username_el = page.query_selector('input[name="username"]')
            password_el = page.query_selector('input[name="password"]')

            if username_el:
                current_user = username_el.input_value() or ''
                if username and current_user != username:
                    username_el.click()
                    page.wait_for_timeout(200)
                    username_el.fill(username)
            elif not username:
                print("[登录] 用户名为空且未预填")
                continue

            if password_el:
                current_pass = password_el.input_value() or ''
                if password and current_pass != password:
                    password_el.click()
                    page.wait_for_timeout(200)
                    password_el.fill(password)
            elif not password:
                print("[登录] 密码为空且未预填")
                continue

            # 勾选"记住账号"
            page.evaluate("""() => {
                const cb = document.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
            }""")

            # 滑块验证
            slider = SliderHandler(page)
            slider_ok = slider.solve(max_retries=2)
            if not slider_ok:
                print("[登录] 滑块验证结果未确认，检查是否已自动登录...")
                page.wait_for_timeout(2000)
                current_url = page.url
                if '/auth/login' not in current_url:
                    print("[登录] 滑块后已自动跳转，视为登录成功")
                    return True
                token_check = page.evaluate(
                    """() => {
                      const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                      return !!access.accessToken;
                    }"""
                )
                if token_check:
                    print("[登录] 滑块后Token已存在，视为登录成功")
                    return True
                continue

            # 点击登录按钮
            login_btn = page.query_selector('button:has-text("登录")')
            if login_btn:
                login_btn.click()

            # 等待页面跳转（轮询）
            for check_i in range(20):
                page.wait_for_timeout(1000)
                current_url = page.url
                if '/auth/login' not in current_url:
                    print("[登录] 登录成功（URL已跳离登录页）")
                    return True
                token_check = page.evaluate(
                    """() => {
                      const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                      return !!access.accessToken;
                    }"""
                )
                if token_check:
                    print("[登录] 登录成功（Token已写入localStorage）")
                    return True

            # 检查登录结果
            body = page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
            if '账号或密码错误' in body:
                print("[登录] 账号或密码错误")
                return False
            if '验证通过' in body:
                print("[登录] 仍在登录页，滑块已过但登录未跳转")
                continue

            print(f"[登录] 未成功，URL: {page.url}")

        except Exception as exc:
            print(f"[登录] 异常: {exc}")
            page.wait_for_timeout(2000)

    print("[登录] 所有重试均失败")
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
            print('[弹窗] 未检测到"请登录"按钮')
            return False

        login_button_uid = _get_popup_button_uid(target_page)
        if not login_button_uid:
            return False

        before_count = len(context.pages)

        # 点击"请登录"按钮
        _click_popup_button(target_page)
        target_page.wait_for_timeout(3000)

        # 检测是否有新登录页
        new_pages = [p for p in context.pages if len(context.pages) > before_count]
        login_page = None
        if new_pages:
            # 取最后一个（最新创建的页面）
            login_page = context.pages[-1]
            try:
                if MAOZI_AUTH_URL in login_page.url.lower():
                    pass  # 确认是登录页
                else:
                    login_page = None
            except Exception:
                login_page = None

        if login_page:
            print("[弹窗] 检测到新登录页，执行自动登录...")
            success = auto_login(login_page)
            if success:
                try:
                    login_page.close()
                except Exception:
                    pass
            return success

        # 等待弹窗消失
        target_page.wait_for_timeout(2000)
        if not _get_popup_button_uid(target_page):
            print("[弹窗] 弹窗已消失，视为登录成功")
            return True

        target_page.wait_for_timeout(2000)

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
                if auto_login(page):
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
    try:
        login_page = context.new_page()
        ok = auto_login(login_page)
        if ok:
            print("[恢复] 方法3 成功")
            return True
        try:
            login_page.close()
        except Exception:
            pass
    except Exception as exc:
        print(f"[恢复] 方法3异常: {exc}")

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
