# [OPEN] sku3-batch-debug

## 目标
- 使用 MCP 工具读取 `sku3` 真实请求包、请求头、载荷与响应。
- 判断当前链路是整批失败、部分成功还是字段缺失。
- 测试一条更贴近插件卡片渲染行为的高效 `sku3` 获取路径。

## 假设
1. 当前批量 `sku3` 注入脚本与插件真实卡片请求头或 token 来源不一致，导致批量请求失败。
2. 当前批量路径并非整批都失败，而是部分 SKU 返回成功，但上层卖家逻辑把异常放大成整批跳过。
3. 当前扩展页里的批量脚本存在运行时错误，导致 `page.evaluate(...)` 阶段直接抛错。
4. 插件卡片渲染使用的并不是当前批量函数同一条路径，至少在页面上下文、`Client` 标识或 `credentials` 上存在差异。
5. 当前失败可能是挑战页、超时或非 JSON 响应，而不是接口业务字段缺失。

## 当前状态
- 已开始通过 MCP 采集运行时证据。

## 新证据
- 已确认 `9222` 真实浏览器会话存在，并包含 `Ozon` 首页、卖家页、毛子 ERP 页和插件 `popup.html` 页面。
- 已通过附着 `9222` 的 Playwright/CDP 会话，先打开 `Ozon` 首页，再打开卖家页 `https://www.ozon.ru/seller/vernyy-lux-verny-lux/`。
- 已从真实卖家页提取出多个 SKU：`3259280935`、`3222119771`、`3243767651` 等。
- 已在真实插件 `popup.html` 页面中读取到 `chrome.storage.local['maozierp-token']`，token 存在。
- 使用站点页 `localStorage['maozierp-core-access'].accessToken` 直接请求 `api.chrome/sku3` 返回 `401 {"code":-1,"msg":"No token find"}`，说明站点 token 不是该接口认可的插件 token。
- 使用真实插件页 + `maozierp-token` + 请求头 `Client: plugin`、`Plugin-Version: 2.3.2` 发起单 SKU 请求时，`sku=3259280935` 返回 `200`，响应为 `{"code":1,"msg":"okc",...}`。
- 使用修正后的批量脚本在真实插件页测试 3 个 SKU 时，3 个全部返回 `200`，说明“插件式批量路径”本身是可行的。

## 当前结论
1. 当前高效且可靠的 `sku3` 获取路径应以“真实插件页 + chrome.storage.local 的 maozierp-token + Client=plugin + Plugin-Version”为准。
2. 站点页 `accessToken` 不能替代插件 token，不能作为 `api.chrome/sku3` 的稳定生产路径。
3. 当前项目里的批量失败，更像是现有批量注入实现问题，而不是接口天然不支持批量或只能部分成功。

## 已收集证据
- MCP 在 `https://ozon.maozierp.com/#/selection/top-list` 页面读取到 `localStorage["maozierp-core-access"]`，其中存在 `accessToken`。
- 使用站点页 `accessToken` 发起 `POST https://api.maozierp.com/api.chrome/sku3?sku=1608725864`：
  - 请求头包含 `Authorization: Bearer <accessToken>`、`Client: pc`
  - 请求体为 `{"sku":"1608725864"}`
  - 响应为 `401`，返回 `{"code":-1,"msg":"No token find"}`
- 使用同一 `accessToken` 改成插件样式请求头 `Client: plugin` + `Plugin-Version: 2.3.2`：
  - 网络层表现为 `net::ERR_FAILED`
  - 页面随后进入 Cloudflare 阻断页
- 插件源码证据：
  - `content.js` 里的 `Zg()` 通过 `fn({ path: "/api.chrome/sku3?sku="+sku, method:"POST", body:{sku} })` 取数
  - `fn()` 从 `chrome.storage.local` 读取 `maozierp-token`
  - `background.js` 里的 `API_REQUEST` 再用该 token 代理 `fetch` 到毛子接口
- 当前批量脚本可疑点：
  - `browser_ozon.py` 的 `_fetch_top_list_sku3_batch()` 主分支使用了未定义的 `items` 变量，存在运行时直接抛错的高风险

## 中间结论
- 插件真实可用路径依赖 `chrome.storage.local` 中的 `maozierp-token`，不是站点页 `localStorage.accessToken` 直连。
- 站点页 token 路径对 `api.chrome/sku3` 不成立，至少当前返回 `401 No token find`。
- 伪装插件请求头但仍使用站点页 token 的方式不可靠，并可能触发更强风控。
- 下一步应优先验证/修复真实插件路径上的批量脚本，而不是继续放大站点页 token 方案。
