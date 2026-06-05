# AI Agent 接入指南：连接采集专用 Chrome 浏览器（CDP 9222）

## 一、背景

项目运行时，Chrome 浏览器通过 CDP（Chrome DevTools Protocol）暴露在 `http://127.0.0.1:9222`。所有采集任务（榜单、SKU3、卖家主页）都通过注入 JS 到这个浏览器中来调用 API。

AI Agent 需要能够**接入这个浏览器进行实时监控和调试**，而不是启动自己的浏览器实例。

---

## 二、Chrome 启动方式

### Windows

```powershell
# 项目内置方案：GUI 中点击"启动浏览器"按钮
# 或命令行：
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\project\maozi-plugin-2.3.2\ozon_selection_pipeline\profiles\profile-001" `
  --disable-blink-features=AutomationControlled `
  --no-first-run `
  --no-default-browser-check
```

### Linux（lx 服务器）

```bash
#!/bin/bash
# 1. 启动虚拟显示（如果无桌面）
Xvfb :99 -ac -screen 0 1920x1080x24 &

# 2. 启动 Chrome
DISPLAY=:99 ~/chrome/opt/google/chrome/chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=~/ozon_selection_pipeline/profiles/server-profile \
    --no-first-run --no-sandbox --disable-dev-shm-usage \
    --disable-gpu --headless=new \
    --disable-extensions --disable-blink-features=AutomationControlled \
    --window-size=1920,1080 about:blank
```

---

## 三、验证 CDP 是否可用

```bash
# 检查浏览器版本（返回 JSON 说明正常）
curl http://127.0.0.1:9222/json/version

# 列出所有打开的页面
curl http://127.0.0.1:9222/json/list
```

正常响应示例：
```json
[
  {
    "id": "AAD48A12C73B...",
    "title": "榜单选品 - 毛子ERP",
    "url": "https://ozon.maozierp.com/#/selection/top-list",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/..."
  },
  {
    "id": "3AB0AAD8226D...",
    "title": "OZON маркетплейс...",
    "url": "https://www.ozon.ru/",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/..."
  }
]
```

浏览器正常运行时，至少应该有 **1 个毛子页面 + 1 个 Ozon 页面**。

---

## 四、AI Agent 接入方法

### 方法 A：Playwright 连接 CDP（推荐）

```python
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
context = browser.contexts[0]

# 获取所有打开的页面
pages = context.pages
for pg in pages:
    print(f"  {pg.url} | {pg.title()}")

# 在特定页面上执行 JS
maozi_page = [p for p in pages if 'ozon.maozierp.com' in p.url][0]
result = maozi_page.evaluate("""
    () => {
        const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
        return {
            hasToken: !!access.accessToken,
            tokenLen: (access.accessToken || '').length,
            pageUrl: location.href
        };
    }
""")
print(result)

browser.close()
p.stop()
```

**关键注意事项**：
- 使用 `connect_over_cdp`，**不要** `launch` 或 `launch_persistent_context`
- 不要调用 `browser.close()` —— 它会关闭用户的浏览器！
- 用完只 `p.stop()` 断开 Playwright 连接

### 方法 B：直接 WebSocket（底层）

```python
import json, requests
from websocket import create_connection

# 1. 获取页面 WebSocket URL
pages = requests.get("http://127.0.0.1:9222/json/list").json()
ws_url = pages[0]["webSocketDebuggerUrl"]

# 2. 连接
ws = create_connection(ws_url)

# 3. 发送 CDP 命令（Runtime.evaluate 执行 JS）
ws.send(json.dumps({
    "id": 1,
    "method": "Runtime.evaluate",
    "params": {
        "expression": "document.title",
        "returnByValue": True
    }
}))
print(ws.recv())

ws.close()
```

---

## 五、常用诊断操作

### 检查毛子登录状态

```python
# 检查 localStorage Token
token_info = page.evaluate("""
    () => {
        const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
        return {
            hasToken: !!access.accessToken,
            tokenLen: (access.accessToken || '').length,
            prefix: (access.accessToken || '').slice(0, 20)
        };
    }
""")
```

### 检查 Ozon 插件状态

```python
# 检查插件 shadow DOM 中是否有"请登录"按钮
plugin_state = page.evaluate("""
    () => {
        const host = document.querySelector('MAOZIERP-UI');
        if (!host || !host.shadowRoot) return 'NO_PLUGIN';
        for (const btn of host.shadowRoot.querySelectorAll('button')) {
            if (btn.innerText?.trim() === '请登录') return 'NEED_LOGIN';
        }
        return 'LOGGED_IN';
    }
""")
# 返回: 'NO_PLUGIN' | 'NEED_LOGIN' | 'LOGGED_IN'
```

### 测试 SKU3 API

```python
# 直接从浏览器页面调 API（绕过所有 Python 代码）
result = page.evaluate("""
    async () => {
        const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
        const token = access.accessToken || '';
        try {
            const r = await fetch('https://api.maozierp.com/api.chrome/sku3?sku=175924376', {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Client': 'pc',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({sku: '175924376'})
            });
            const text = await r.text();
            return {ok: r.ok, status: r.status, text: text.slice(0, 200)};
        } catch(e) {
            return 'FETCH_ERROR: ' + e.message;
        }
    }
""")
```

### 测试榜单 API

```python
result = page.evaluate("""
    async () => {
        const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
        const token = access.accessToken || '';
        try {
            const r = await fetch('https://api.maozierp.com/api.selection.top/lists?mainType=hot&page=1&page_size=3', {
                headers: {'Authorization': `Bearer ${token}`, 'Client': 'pc', 'X-Client-Type': 'pc'}
            });
            const text = await r.text();
            return {ok: r.ok, status: r.status, text: text.slice(0, 200)};
        } catch(e) {
            return 'FETCH_ERROR: ' + e.message;
        }
    }
""")
```

---

## 六、常见问题诊断速查表

| 症状 | 诊断命令 | 常见原因 |
|------|---------|---------|
| 采集卡住不动 | 检查进程 `tasklist \| findstr python` | DB 超时 / CDP 连接断开 |
| SKU3 全返回 FETCH_ERROR | 测试 SKU3 API（见上） | `Client: plugin` 头导致 CORS 失败 |
| 榜单 API 返回 401 | 检查 Token `token_info`（见上） | Token 过期，需要重新登录 |
| 毛子网页打不开 | `curl -s --max-time 5 https://ozon.maozierp.com` | 网络问题 / 毛子服务挂了 |
| CDP 不可达 | `curl http://127.0.0.1:9222/json/version` | Chrome 没启动 / 端口被占用 |
| 内存溢出 | `tasklist \| findstr chrome` | Chrome 运行太久需重启 |

---

## 七、安全原则

1. **不要 kill Chrome**：AI Agent 只连接 CDP，不管理浏览器生命周期
2. **不要 close browser**：`connect_over_cdp` 断开时用 `p.stop()`，不要 `browser.close()`
3. **只读优先**：先 `evaluate` 读取状态，再决定是否修改
4. **Token 不落地**：不要在日志/文件中打印完整 Token
5. **凭证不入 Git**：`.env` 已加入 `.gitignore`，MAOZI_USERNAME/PASSWORD 不在版本库中
