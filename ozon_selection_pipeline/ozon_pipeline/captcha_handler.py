"""拟人化滑块验证码处理器 - 适配毛子ERP vben-spine滑块"""
from __future__ import annotations

import math
import random
import time
from typing import Any

import logging

log = logging.getLogger(__name__)


class SliderHandler:
    """拟人化滑块拖动处理器
    
    针对毛子ERP登录页面的vben-spine滑块验证：
    - 查找 .vben-spine-text 元素
    - 从其父级的父级获取轨道尺寸
    - 从轨道左边缘拟人化拖动到右边缘
    """
    
    def __init__(self, page: Any) -> None:
        self.page = page
    
    def solve(self, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                # 检查是否已经通过验证
                if self._already_verified():
                    log.debug("滑块已通过验证")
                    return True
                
                # 获取滑块轨道信息
                track = self._get_track_info()
                if not track:
                    log.debug("未找到滑块轨道")
                    return False
                
                # 计算拖动参数
                start_x = track['x'] + 20
                start_y = track['y'] + track['h'] / 2
                end_x = track['x'] + track['w'] - 20
                
                log.debug(f"拖动滑块: ({start_x:.0f},{start_y:.0f}) -> ({end_x:.0f},{start_y:.0f}) 距离={end_x-start_x:.0f}px")
                
                # 执行拟人化拖动
                self._human_like_drag(start_x, start_y, end_x)
                
                # 等待并检查结果
                time.sleep(0.8)
                if self._check_result():
                    return True
                
                log.warning(f"滑块验证未通过 (尝试 {attempt + 1}/{max_retries})")
                time.sleep(random.uniform(0.5, 1.5))
                
            except Exception as e:
                log.warning(f"滑块处理失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                try:
                    self.page.mouse.up()
                except Exception:
                    pass
                time.sleep(1)
        
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
        
        # 1. 移动到起始位置
        self.page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.1, 0.2))
        
        # 2. 按下
        self.page.mouse.down()
        time.sleep(random.uniform(0.05, 0.1))
        
        # 3. 生成轨迹并移动
        steps = random.randint(40, 60)
        for i in range(1, steps + 1):
            progress = i / steps
            
            # 慢-快-慢缓动曲线
            if progress < 0.15:
                eased = progress * 0.6
            elif progress < 0.85:
                eased = 0.09 + (progress - 0.15) * 1.1
            else:
                eased = 0.86 + (progress - 0.85) * 0.93
            eased = min(eased, 1.0)
            
            cx = start_x + distance * eased
            cy = start_y + random.gauss(0, 1.5)
            
            # 时间间隔不均匀
            dt = 0.015 + random.uniform(0, 0.015)
            if progress > 0.9:
                dt += random.uniform(0.01, 0.03)
            
            self.page.mouse.move(cx, cy)
            time.sleep(dt)
        
        # 4. 最终对齐
        self.page.mouse.move(end_x, start_y)
        time.sleep(random.uniform(0.03, 0.08))
        
        # 5. 释放
        self.page.mouse.up()
    
    def _check_result(self) -> bool:
        try:
            # 等待更长时间，多次检查
            for _ in range(8):
                time.sleep(0.5)
                text = self.page.evaluate("""() => {
                    const s = document.querySelector('.vben-spine-text');
                    if (!s) return '__ELEMENT_GONE__';
                    return s.innerText || '';
                }""")
                if text == '验证通过':
                    return True
                # 元素消失=滑块已完成且页面已变化，等同于通过
                if text == '__ELEMENT_GONE__':
                    log.debug("滑块元素已消失，视为验证通过")
                    return True
            return False
        except Exception as e:
            log.debug(f"检查结果失败: {e}")
            return False


def detect_captcha_required(page: Any) -> bool:
    """检测是否需要滑块验证
    
    检查 .vben-spine-text 是否显示"请按住滑块拖动"
    """
    try:
        text = page.evaluate("""() => {
            const spine = document.querySelector('.vben-spine-text');
            if (!spine) return '';
            const parent = spine.parentElement;
            if (!parent) return '';
            const style = window.getComputedStyle(parent);
            if (style.display === 'none' || style.visibility === 'hidden') return '';
            return spine.innerText;
        }""")
        return '请按住滑块拖动' in text
    except Exception:
        return False


def detect_captcha_present(page: Any) -> bool:
    """检测是否出现验证码（需要拖动）"""
    return detect_captcha_required(page)


def detect_login_expired(page: Any) -> bool:
    """检测登录是否失效
    
    检测特征：
    1. 页面跳转到登录页
    2. 出现"请登录"提示
    3. Token过期错误
    """
    try:
        state = page.evaluate("""() => {
            const url = location.href;
            const body = document.body?.innerText || '';
            return {
                isLoginPage: url.includes('/login') || url.includes('/auth'),
                hasLoginPrompt: body.includes('请登录') || body.includes('Please login'),
                hasSessionExpired: body.includes('会话过期') || body.includes('session expired'),
                hasTokenError: body.includes('token') && body.includes('expired'),
            };
        }""")
        
        return any([
            state.get('isLoginPage'),
            state.get('hasLoginPrompt'),
            state.get('hasSessionExpired'),
            state.get('hasTokenError'),
        ])
    except Exception:
        return False


_LOGIN_URL = "https://ozon.maozierp.com/#/auth/login"


def auto_login(
    page: Any,
    username: str | None = None,
    password: str | None = None,
    max_retries: int = 3,
) -> bool:
    """自动登录毛子ERP
    
    流程：
    1. 导航到登录页
    2. 等待SPA渲染完成
    3. 检查是否需要填账号密码（可能已预填）
    4. 拖动滑块完成验证
    5. 点击登录按钮
    6. 等待页面跳转
    
    Args:
        page: Playwright Page 对象
        username: 用户名（None则使用预填值）
        password: 密码（None则使用预填值）
        max_retries: 最大重试次数
        
    Returns:
        bool: 是否登录成功
    """
    import random as _random
    
    for attempt in range(max_retries):
        try:
            # 1. 导航到登录页
            log.info(f"导航到登录页 (尝试 {attempt + 1}/{max_retries})")
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            
            # 2. 等待SPA渲染完成
            for _ in range(30):
                body_text = page.evaluate("() => document.body?.innerText || ''")
                if '请按住滑块拖动' in body_text or '请登录' in body_text:
                    break
                page.wait_for_timeout(300)
            
            # 3. 检查并填写账号密码
            username_el = page.query_selector('input[name="username"]')
            password_el = page.query_selector('input[name="password"]')
            
            if username_el:
                current_user = username_el.input_value() or ''
                if username and current_user != username:
                    username_el.click()
                    page.wait_for_timeout(200)
                    username_el.fill(username)
                    log.debug(f"填入用户名: {username}")
                elif not username and not current_user:
                    log.warning("用户名为空且未预填，登录将失败")
            
            if password_el:
                current_pass = password_el.input_value() or ''
                if password and current_pass != password:
                    password_el.click()
                    page.wait_for_timeout(200)
                    password_el.fill(password)
                    log.debug("填入密码")
                elif not password and not current_pass:
                    log.warning("密码为空且未预填，登录将失败")
            
            # 4. 勾选记住账号
            page.evaluate("""() => {
                const cb = document.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
            }""")
            
            # 5. 拖动滑块
            handler = SliderHandler(page)
            slider_ok = handler.solve(max_retries=2)
            if not slider_ok:
                log.warning("滑块验证未确认通过，检查是否已自动登录...")
                # 滑块可能实际已通过，检查登录状态
                page.wait_for_timeout(2000)
                current_url = page.url
                title = page.title()
                if '/login' not in current_url and '登录' not in title:
                    log.info("滑块后已自动跳转，视为登录成功")
                    return True
                token_check = page.evaluate(
                    """() => {
                      const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                      return !!access.accessToken;
                    }"""
                )
                if token_check:
                    log.info("滑块后Token已存在，视为登录成功")
                    return True
                log.warning("滑块未完成，重试...")
                continue
            
            # 6. 点击登录
            log.debug("点击登录按钮")
            login_btn = page.query_selector('button:has-text("登录")')
            if login_btn:
                login_btn.click()
            
            # 7. 等待页面跳转（SPA hash路由可能较慢，轮询等待）
            for check_i in range(20):
                page.wait_for_timeout(1000)
                current_url = page.url
                title = page.title()
                # 检查是否已经跳离登录页
                if '/login' not in current_url and '登录' not in title:
                    log.info("登录成功（URL已跳离登录页）")
                    return True
                # 或者 localStorage 已有有效 token
                token_check = page.evaluate(
                    """() => {
                      const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
                      return !!access.accessToken;
                    }"""
                )
                if token_check:
                    log.info("登录成功（Token已写入localStorage）")
                    return True
                if check_i > 4:
                    log.debug(f"等待登录跳转... URL={current_url} title={title}")
            
            # 8. 检查登录结果
            body = page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
            current_url = page.url
            title = page.title()
            
            if '/login' not in current_url and '登录' not in title:
                log.info("登录成功")
                return True
            
            if '账号或密码错误' in body:
                log.error("账号或密码错误")
                return False
            
            if '验证通过' in body:
                log.warning("仍在登录页，滑块已过但登录未跳转")
                continue
            
            log.warning(f"登录未成功，当前URL: {current_url}，标题: {title}")
            
        except Exception as e:
            log.error(f"登录异常 (尝试 {attempt + 1}): {e}")
            page.wait_for_timeout(2000)
    
    return False


def handle_plugin_login_popup(page: Any, context: Any) -> bool:
    """处理毛子ERP插件右下角'请登录'弹窗
    
    流程：
    1. 检测MAOZIERP-UI shadow DOM中的'请登录'按钮
    2. 点击按钮
    3. 监测是否打开新的登录页
    4. 如果是新登录页：拖动滑块、登录、关闭登录页
    5. 如果无新页面：检查弹窗是否消失
    
    Args:
        page: 当前Ozon页面的Playwright Page对象
        context: 浏览器Context对象（用于监测新页面）
        
    Returns:
        bool: 是否处理成功
    """
    import random as _random
    
    # 1. 检测插件弹窗
    popup_info = page.evaluate("""() => {
        const host = document.querySelector('MAOZIERP-UI');
        if (!host || !host.shadowRoot) return null;
        
        const buttons = host.shadowRoot.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.innerText?.trim() === '请登录') {
                const r = btn.getBoundingClientRect();
                return {found: true, x: r.x + r.width/2, y: r.y + r.height/2};
            }
        }
        return {found: false};
    }""")
    
    if not popup_info or not popup_info.get('found'):
        return False
    
    log.info("检测到插件'请登录'弹窗")
    
    # 2. 记录当前页面
    known_page_ids = {p.url for p in context.pages if not p.is_closed()}
    
    # 3. 点击按钮
    clicked = page.evaluate("""() => {
        const host = document.querySelector('MAOZIERP-UI');
        if (!host || !host.shadowRoot) return false;
        const buttons = host.shadowRoot.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.innerText?.trim() === '请登录') {
                btn.click();
                return true;
            }
        }
        return false;
    }""")
    
    if not clicked:
        return False
    
    log.debug("已点击'请登录'按钮")
    
    # 4. 等待新登录页打开或弹窗消失
    login_page = None
    for _ in range(15):  # 最多等15秒
        page.wait_for_timeout(1000)
        
        # 检查新页面
        for pg in context.pages:
            if pg.is_closed():
                continue
            if pg.url not in known_page_ids and '/auth/login' in pg.url:
                login_page = pg
                log.info("检测到登录页打开")
                break
        
        if login_page:
            break
        
        # 检查弹窗是否已消失（无新页面但弹窗没了也算成功）
        still_there = page.evaluate("""() => {
            const host = document.querySelector('MAOZIERP-UI');
            if (!host || !host.shadowRoot) return false;
            const buttons = host.shadowRoot.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.innerText?.trim() === '请登录') return true;
            }
            return false;
        }""")
        
        if not still_there:
            log.info("'请登录'弹窗已消失")
            return True
    
    # 5. 如果打开了登录页，处理登录
    if login_page:
        log.info("处理登录页...")
        try:
            login_page.bring_to_front()
            page.wait_for_timeout(1500)
            
            # 等待渲染
            for _ in range(20):
                body = login_page.evaluate("() => document.body?.innerText || ''")
                if any(k in body for k in ('请按住滑块拖动', '请登录', '验证通过')):
                    break
                page.wait_for_timeout(300)
            
            # 调用auto_login处理滑块和登录
            success = auto_login(login_page)
            
            if success:
                log.info("插件登录成功，关闭登录页")
                login_page.close()
                page.wait_for_timeout(500)
                return True
            else:
                log.warning("自动登录未成功")
                return False
                
        except Exception as e:
            log.error(f"处理登录页异常: {e}")
            try:
                login_page.close()
            except Exception:
                pass
            return False
    
    # 6. 没有新页面——再次检查弹窗状态
    still_there = page.evaluate("""() => {
        const host = document.querySelector('MAOZIERP-UI');
        if (!host || !host.shadowRoot) return false;
        const buttons = host.shadowRoot.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.innerText?.trim() === '请登录') return true;
        }
        return false;
    }""")
    
    if not still_there:
        log.info("'请登录'弹窗最终消失")
        return True
    
    log.warning("弹窗仍在且无新登录页")
    return False


def ensure_authenticated(context: Any, max_retries: int = 3) -> bool:
    """确保浏览器已登录，自动处理登录失效、滑块验证、插件弹窗
    
    检测并修复登录状态：
    1. 扫描所有页面，查找登录页（/auth/login）处理滑块+登录
    2. 扫描ozon.ru页面，处理插件"请登录"弹窗
    3. 登录成功后关闭多余的dashboard页面
    4. 重试 max_retries 次
    
    Args:
        context: Playwright browser context
        max_retries: 最大尝试次数（至少2次以保证一轮处理一轮验证）
        
    Returns:
        bool: 是否已认证成功
    """
    effective_retries = max(max_retries, 2)
    for attempt in range(effective_retries):
        try:
            needs_recovery = False
            
            # 扫描所有页面
            for page in list(context.pages):
                if page.is_closed():
                    continue
                
                url = page.url or ''
                
                 # 情况1：登录页需要滑块
                if '/auth/login' in url:
                    log.info(f"检测到登录页需要处理 (尝试 {attempt + 1})")
                    page.bring_to_front()
                    page.wait_for_timeout(1000)
                    success = auto_login(page)
                    if success:
                        log.info("登录页登录成功，等待页面跳转...")
                        # 等待SPA完成跳转（最多10秒）
                        for _ in range(10):
                            page.wait_for_timeout(1000)
                            if '/auth/login' not in (page.url or ''):
                                break
                        # 关闭残留登录页
                        try:
                            if '/auth/login' in (page.url or ''):
                                page.goto("https://ozon.maozierp.com/#/dashboard", wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        _close_extra_dashboard_pages(context)
                        log.info("认证已恢复")
                        return True
                    else:
                        log.warning("登录页自动登录失败")
                    needs_recovery = True
                    break
                
                # 情况2：Ozon页面有插件弹窗
                if 'ozon.ru' in url:
                    result = handle_plugin_login_popup(page, context)
                    if result:
                        log.info("插件弹窗登录成功")
                        needs_recovery = True
                        break
            
            # 一轮处理完后检查是否仍需要恢复
            if not needs_recovery:
                # 检查是否还有未处理的登录页
                still_has_login = False
                for page in context.pages:
                    if page.is_closed():
                        continue
                    if '/auth/login' in (page.url or ''):
                        still_has_login = True
                        break
                    if 'ozon.ru' in (page.url or ''):
                        popup_exists = page.evaluate("""() => {
                            const host = document.querySelector('MAOZIERP-UI');
                            if (!host || !host.shadowRoot) return false;
                            for (const btn of host.shadowRoot.querySelectorAll('button')) {
                                if (btn.innerText?.trim() === '请登录') return true;
                            }
                            return false;
                        }""")
                        if popup_exists:
                            still_has_login = True
                            break
                
                if not still_has_login:
                    log.info("认证状态正常")
                    return True
            
            page.wait_for_timeout(2000)
            
        except Exception as e:
            log.warning(f"ensure_authenticated 异常 (尝试 {attempt + 1}): {e}")
            page.wait_for_timeout(2000)
    
    log.error(f"认证恢复失败，已尝试 {effective_retries} 次")
    return False


def _close_extra_dashboard_pages(context: Any) -> None:
    """关闭多余的dashboard页面，只保留需要的"""
    dashboard_pages = []
    for page in context.pages:
        if page.is_closed():
            continue
        url = page.url or ''
        if '/dashboard' in url or ('ozon.maozierp.com' in url and '/auth/login' not in url):
            dashboard_pages.append(page)
    
    # 保留一个，关闭多余的
    for page in dashboard_pages[:-1] if len(dashboard_pages) > 1 else []:
        try:
            log.debug(f"关闭多余页面: {page.url}")
            page.close()
        except Exception:
            pass
