# 修改日志 (Fix Log)

---

## 2026-06-08 15:30

**Git Commit**: `09a325d`

**修改文件列表**：
- `maozi_collect_mini/seller_collector.py`
- `maozi_collect_mini/browser.py`

**修改目的**：
修复卖家主页采集只对前 60 个 SKU 调用 SKU3 的问题（`skus[:60]` 硬编码），改为分批全部调用。同时从 entrypoint API 的 tile JSON 中提取更多字段（`stock_max`、`badge`、`brand_logo_url`），为未来可能的扩展提供数据基础。

**方案选择原因**：
原代码 `skus[:60]` 一刀切——一个 500 多 SKU 的卖家翻页 72 次拿回全部商品列表，却只用了前 60 个调 SKU3，其余全部被丢弃。修复方案：按 `top_list_sku3_batch_size`（默认 60）分批循环调用 SKU3，所有批次的返回结果合并到 `all_sku3_results`，批次间有延迟避免限流，单批失败不中断继续下一批。

库存预筛最初考虑加入但被移除——用户业务模式是先卖后找供应商，库存无关紧要。

**具体修改内容**：
1. `browser.py` `fetch_seller_home_products()` — 每个 tile 额外提取 `stock_max`（从 `multiButton.ozonButton.addToCart.quantityButton.maxItems`）、`badge`（从 `tileImage.leftBottomBadgeV2.text`）、`brand_logo_url`（从 `brandLogo.logo`），写入 item 字典。
2. `seller_collector.py`：
   - SKU3 调用：`skus[:60]` 单批 → `for batch_idx in range(0, len(skus), batch_size)` 循环分批，`all_sku3_results.update(batch_results)` 合并
   - 每批打印进度：`SKU3批次[1/10]: 成功58 失败2`
   - 批次间 `time.sleep(batch_delay/1000)` 避免限流
   - `sku3_results` 引用全部改为 `all_sku3_results`
   - 使用 `settings.top_list_sku3_batch_size/concurrency/chunk_delay_ms` 配置项

---

## 2026-06-07 23:50

**修改文件列表**：
- `AGENTS.md`

**修改目的**：
新增三条强制规则：修改日志追踪规范，确保每轮修改可追溯、可回溯。

**方案选择原因**：
用户要求建立修改追踪机制，采用 Markdown 文件记录比 Git 提交日志更灵活，可在同文件内附注决策原因。fix_log.md 放在项目根目录便于快速查阅。

**具体修改内容**：
1. 在 `AGENTS.md` 模块二末尾追加 **【强制】修改日志记录与追踪规范** 章节，包含：
   - 规则1：每轮修改必须追加记录到 `fix_log.md`（含时间、文件、目的、方案原因、内容五个字段）
   - 规则2：修改前必读 `fix_log.md`，了解历史避免冲突
   - 规则3：每轮回答以"以上为本轮修改及结论"结尾
2. 创建 `fix_log.md` 初始文件（本文档）

---

## 2026-06-08 00:00

**修改文件列表**：
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
榜单阶段不再执行 0325 优质品判定，只做榜单种子扩展规则预筛。预筛通过的种子写入 `seed_pool_skus`，同时将其卖家写入 `seller_shops`。淘汰榜单阶段的 SKU3 获取和 evaluate_selection_rule 调用。

**方案选择原因**：
榜单阶段的目的是广泛撒网发现潜在卖家，0325 优质品判定依赖 SKU3 API，而 SKU3 返回字段经常为空（soldCount、createDays 等），导致即便预筛通过 48/50 条，正式判定也 0/48 全部淘汰。两个阶段应职责分离：榜单阶段负责种子发现和卖家记录，卖家主页阶段负责精确筛选和商品入库。

**具体修改内容**：
1. 删除整个 SKU3 获取 + `evaluate_selection_rule` 判定块（约80行）
2. 预筛通过的种子 → 写入 `seed_pool_skus` 后，直接遍历种子将其 `seller_id` 写入 `seller_shops`
3. 预筛淘汰的 SKU 也执行 `mark_seed_status("rejected")`
4. stats 字典 `products_qualified` → `sellers_recorded`
5. 移除未使用导入：`parse_sku3_response`、`evaluate_selection_rule`、`DEFAULT_SELECTION_RULE`、`settings`
6. 更新模块 docstring 和函数返回值文档

---

## 2026-06-08 01:00

**修改文件列表**：
- `maozi_collect_mini/rules.py`
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
1. 种子规则取消退货率限制（redemption_rate 在榜单阶段不可获取，不应作为种子筛选条件）
2. 预筛通过的种子增加跟卖列表获取：调用 `fetch_seller_offers(sku)` 获取跟卖数量，检查 ≤ 50，通过后将种子自身卖家及跟卖列表中的卖家一并写入 `seller_shops`

**方案选择原因**：
榜单阶段不获取 SKU3，因此无法拿到退货取消率，该字段本就不应在种子规则中。跟卖人数是种子规则的核心条件之一，榜单 API 自身没有此字段，必须额外调用 `sellerOffers` 接口。方案设计为"仅对预筛通过的种子获取跟卖（避免浪费请求）→ 跟卖 ≤ 50 通过 → 种子自身卖家 + 全部跟卖卖家写入 seller_shops"。

**具体修改内容**：
1. `rules.py` — `get_top_list_seed_rule()` 移除 `redemption_rate=RangeRule(maximum=Decimal("5"))`
2. `ranking_collector.py` — 替换卖家记录逻辑：
   - 对每个种子调用 `browser.fetch_seller_offers(sku)`，容错异常
   - 跟卖人数 > 50 → 打印 `"跟卖超标"` 并 `mark_seed_status("rejected")`
   - 跟卖人数 ≤ 50 → 写入种子自身卖家 + 遍历 offers 写入全部跟卖卖家
   - 新增统计：`offers_checked`、`offers_rejected`，日志输出 `"卖家写入: X个 (跟卖检查Y个种子, 超标Z)"`
   - 兼容 snake_case/camelCase 字段名（seller_id/sellerId, seller_name/sellerName）

---

## 2026-06-08 01:30

**修改文件列表**：
- `maozi_collect_mini/ranking_collector.py`
- `maozi_collect_mini/gui.py`

**修改目的**：
榜单API上架日期参数改为动态默认值：`create_date_to` 默认为昨天，`create_date_from` 默认为昨天前推200天（即总共200天的上架窗口）。

**方案选择原因**：
硬编码 `"2025-04-01"` 会导致上架日期窗口越来越宽，且每天新上架商品尚无销量数据，截止日期选"今天"不合理。采用 `timedelta` 动态计算：截止昨天（避免无销量新品），起始为昨天-200天，保持200天固定窗口。

**具体修改内容**：
1. `ranking_collector.py` — `_build_base_filters()`:
   - `create_date_to` 默认值：`datetime.now().date() - timedelta(days=1)` (昨天)
   - `create_date_from` 默认值：`datetime.now().date() - timedelta(days=201)` (昨天前推200天)
   - 新增 `from datetime import timedelta`
2. `gui.py` — `_build_ui()`:
   - `create_date_from` 输入框默认值：同上动态计算
   - `create_date_to` 输入框默认值：同上动态计算
   - 新增 `from datetime import timedelta`

---

## 2026-06-08 02:00

**修改文件列表**：
- `maozi_collect_mini/gui.py`

**修改目的**：
消弭 config.json 缓存旧日期覆盖 GUI 动态默认值的冲突：日期字段启动时始终采用代码动态默认值，不从 config.json 持久化加载。

**方案选择原因**：
config.json 仍有必要存在（保存其他配置如 mode/forever/各类阈值），但日期语义是"动态默认值"，不应被缓存中旧值覆盖。方案设计为 `_load_config` 中过滤 `create_date_from`/`create_date_to`，保留 GUI 启动时的动态默认值。用户可在 GUI 中临时修改日期并在当次采集生效（通过 `_save_config` 写回），但下次启动又会自动回到动态默认值。

**具体修改内容**：
1. 新增类属性 `_EPHEMERAL_KEYS = {"create_date_from", "create_date_to"}`
2. `_load_config()` 加载时跳过 `_EPHEMERAL_KEYS` 中的字段
3. 删除旧 `config.json`（其中缓存了过期日期 `2025-10-01`/`2026-06-06`）

---

## 2026-06-08 02:30

**修改文件列表**：
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
修正种子入库顺序：跟卖检查必须在 `bulk_upsert_seed_pool` 之前执行。之前是先入库全部种子再检查跟卖，导致跟卖超标的种子依然滞留在 `seed_pool_skus` 中。

**方案选择原因**：
代码顺序错误：`bulk_upsert_seed_pool` 写在跟卖检查循环外面，无论跟卖是否超标种子都已入库。修改后的正确顺序：预筛 → 逐个获取跟卖 → 跟卖≤50 加入 qualified_seeds → 只对 qualified_seeds 调用 bulk_upsert_seed_pool。

**具体修改内容**：
1. 将 `bulk_upsert_seed_pool` 从循环前移到循环后，只对 `qualified_seeds` 写入
2. 新增 `qualified_seeds` 列表收集通过跟卖检查的种子
3. 日志格式更新：`"预筛总结: 通过X/Y, 跟卖合格Z(超标W), 种子入库N"`

---

## 2026-06-08 15:30

**修改文件列表**：
- `maozi_collect_mini/browser.py`
- `maozi_collect_mini/rules.py`
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
1. 修复 `fetch_seller_offers` 404 问题：`api.maozierp.com/api.chrome/sellerOffers` 端点不存在，改为直接调用 Ozon 开放 API `ozon.ru/api/entrypoint-api.bx/page/json/v2`
2. 在榜单预筛阶段用 `blocked_by_seller` 字段过滤掉卖家已屏蔽（不可跟卖）的 SKU

**方案选择原因**：
1. 毛子插件的 `api.chrome/sellerOffers` 端点返回 404，根本不存在。原始项目 `ozon_selection_pipeline` 一直用的是 Ozon 开放 API 的 entrypoint 接口，通过 `/modal/otherOffersFromSellers?product_id={sku}` 获取跟卖 widget，解析 `webSellerList-*` key 中的 seller 列表。该接口无需 token，只需 `credentials: 'include'`（浏览器 cookie）。
2. 榜单 API 返回中有 `blocked_by_seller` 布尔字段，`true` 表示卖家已屏蔽该商品、其他卖家无法跟卖。应在预筛阶段就淘汰，不必浪费跟卖 API 请求。

**具体修改内容**：
1. `browser.py` — `fetch_seller_offers()` 完全重写：
   - API 从 `POST api.maozierp.com/api.chrome/sellerOffers` 改为 `GET ozon.ru/api/entrypoint-api.bx/page/json/v2?url=...`
   - 解析 `widgetStates` 中以 `webSellerList-` 开头的 key
   - 返回格式改为 `[{seller_name, seller_home_url, seller_id, offer_sku}]`
   - 15s AbortController 超时，失败返回空列表
2. `rules.py` — `evaluate_top_list_prefilter()` 新增 `blocked_by_seller` 检查：
   - 在发货模式检查之前添加：`if item.get("blocked_by_seller") in (True, "true", 1, "1")` → 添加原因 `"卖家已屏蔽，不可跟卖"`
3. `ranking_collector.py` — 跟卖卖家写入逻辑更新：
   - 使用 `offer.get("seller_home_url")` 直接写入（不再拼接 URL）
   - 使用 `offer.get("seller_name")`（不再兼容 camelCase）

---

## 2026-06-08 15:45

**修改文件列表**：
- `maozi_collect_mini/rules.py`
- `maozi_collect_mini/ranking_collector.py`
- `maozi_collect_mini/browser.py`

**修改目的**：
1. 修正 `blocked_by_seller` 过滤逻辑：从预筛淘汰改为"跳过种子卖家写入但保留跟卖检查"
2. 修复跟卖 API 307 CORS 重定向问题：改用 ozon.ru 同源页面发送请求

**方案选择原因**：
1. `blocked_by_seller=true` 一刀切淘汰导致鞋类类目50/50全灭。实际上在 Ozon 平台模式下，原卖家屏蔽商品不等于其他人不能跟卖——跟卖列表中的卖家中仍可能发现合作对象。应保留种子入库，仅跳过原卖家写入。
2. 跟卖请求从 `ozon.maozierp.com` 跨域到 `www.ozon.ru`，307 重定向响应缺少 CORS 头导致浏览器拒绝跟随。解决方案：创建 ozon.ru 同源页面（缓存复用），从同源发送请求，无需跨域。

**具体修改内容**：
1. `rules.py` — `evaluate_top_list_prefilter()` 移除 `blocked_by_seller` 淘汰逻辑（注释保留说明原因）
2. `ranking_collector.py` — 新增 `blocked` 检测和 `blocked_count` 统计：
   - 预筛阶段不再因 blocked_by_seller 淘汰
   - 卖家写入阶段：blocked 时跳过种子自身卖家，但跟卖列表照常写入
   - 日志新增 `", 屏蔽N个(跳过种子卖家)"` 提示
3. `browser.py` — 跟卖请求改为 ozon.ru 同源：
   - 新增 `self._ozon_page` 缓存属性 + `_get_ozon_page()` 方法（与 `get_maozi_page` 对称）
   - `fetch_seller_offers()` 改用 `self._get_ozon_page()` 获取页面，fetch URL 改为相对路径（同源请求，无需跨域）
   - 初始化/清理时一并管理 `_ozon_page` 生命周期

---

## 2026-06-08 16:00

**修改文件列表**：
- `maozi_collect_mini/rules.py`
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
回退 15:45 轮的 blocked_by_seller 逻辑——恢复为"直接淘汰不可跟卖种子"。

**方案选择原因**：
用户纠正：`blocked_by_seller=true` 在 Ozon 平台上意味着该商品任何人都不能跟卖，而非仅原卖家屏蔽。既然不可能有跟卖卖家，种子应直接淘汰，不必浪费 API 请求去获取空的跟卖列表。

**具体修改内容**：
1. `rules.py` — `evaluate_top_list_prefilter()` 恢复 `blocked_by_seller` 淘汰逻辑，原因 `"卖家已屏蔽，不可跟卖"`
2. `ranking_collector.py` — 移除 `blocked_count` 变量和 `blocked` 检测逻辑（种子已在预筛阶段淘汰，此处无需再判断）
   - 恢复种子自身卖家无条件写入
   - 移除 `"屏蔽N个(跳过种子卖家)"` 日志提示

---

## 2026-06-08 16:30

**修改文件列表**：
- `maozi_collect_mini/seller_collector.py`
- `maozi_collect_mini/browser.py`
- `maozi_collect_mini/ranking_collector.py`

**修改目的**：
修复卖家主页采集全部报 `Cannot navigate to invalid URL` —— 数据库中 `home_url` 存储为相对路径（如 `/seller/2497513/`），Playwright 无法导航。

**方案选择原因**：
`fetch_seller_offers` 中 Ozon widget 的 `s.link` 返回相对路径。三层防御：
1. 源头（browser.py `fetch_seller_offers`）：JS 中检测相对路径，自动补 `https://www.ozon.ru` 前缀
2. 写入端（ranking_collector.py）：Python 侧二次检测，防御漏网之鱼
3. 读取端（seller_collector.py）：从数据库读出时归一化，兼容已有脏数据

**具体修改内容**：
1. `browser.py` `fetch_seller_offers()` — JS 侧：`seller_home_url` 检测 `!s.link.startsWith('http')` 则补全前缀
2. `ranking_collector.py` — 跟卖写入循环：`if not o_url.startswith("http")` 补全
3. `seller_collector.py` — 读取 `home_url` 后：`if not home_url.startswith("http")` 补全，处理历史脏数据

---

## 2026-06-09 00:30

**修改文件列表**：
- `maozi_collect_mini/browser.py`
- `maozi_collect_mini/seller_collector.py`

**修改目的**：
用 Ozon entrypoint API 替代之前的 DOM 滚动抓取方案，解决"一个卖家只抓到46条商品"的问题。新方案直接调用 Ozon 后端接口 `api/entrypoint-api.bx/page/json/v2?url=/seller/{id}/`，解析 `widgetStates` 中的 `tileGridDesktop-*` widget 获取商品列表，并支持翻页（`infiniteVirtualPaginator-*` 中的 `nextPage`）。

**方案选择原因**：
DOM 滚动方案存在三个致命缺陷：
1. 滚动次数限制（`max_scrolls=3`）仅抓取3屏商品
2. 商品选择器 `[data-widget="searchResultsV2"]` 与 Ozon 当前页面 DOM 结构不匹配，大部分 tile 的 href 为空
3. 正则提取 SKU `/product/{sku}/` 对空 href 无效，导致 46 条中仅提取出 1-2 个"SKU"（实际是错误解析的数字）

entrypoint API 方案的优点：与原始 `ozon_selection_pipeline` 项目一致（`browser_ozon.py:_fetch_seller_home_products_api`），从 tile JSON 直接获取 sku、title、price、image，无需 DOM 选择器；每页 18-20 个商品，默认翻 10 页可覆盖 180-200 个商品。

**具体修改内容**：
1. `browser.py` `fetch_seller_home_products()` — 完全重写（~100行 → ~130行）：
   - 参数：`max_scrolls` → `max_pages`（默认10）
   - 逻辑：从缓存 ozon 页导航到 seller_url → loop fetch entrypoint API → parse `tileGridDesktop-*` → 提取 `{sku, href, title, price_text, price_amount, currency, image_url}` → 跟随 `nextPage` 翻页
   - 商品字段：sku 直接来自 `tile.sku`/`tile.id`；title 来自 `mainState.textAtom.text`；price 来自 `mainState.priceV2.price[*].text`；image 来自 `tileImage.items[*].image.link`
   - 返回值增加 `pages_fetched` 字段
2. `seller_collector.py`：
   - 调用参数 `max_scrolls=3` → `max_pages=10`
   - SKU 提取：优先 `it.get("sku")`（API 直接返回），兜底 `_extract_sku_from_url`
   - 日志增加翻页次数显示：`"卖家主页获取 X 条商品 (翻页N次)"`
