# [OPEN] ozon-pipeline-run

## 任务目标

- 启动当前采集项目。
- 监督运行过程，确认是否能正常采集数据。
- 记录关键证据、结论与后续处理建议。

## 当前症状

- 用户要求启动当前采集项目并监督执行。
- 目前尚未确认项目是否能正常启动、是否真正发出采集请求、是否成功产出数据。

## 可证伪假设

1. 项目启动失败，根因是依赖、环境变量或启动命令不完整。
2. 项目已启动，但采集入口未触发，请求没有真正发出。
3. 采集请求已发出，但被目标站点拦截、限流或鉴权失败。
4. 采集数据已返回，但解析、保存或后处理阶段失败。
5. 数据已成功采集，但输出路径或展示方式与预期不一致，造成误判。

## 计划步骤

1. 识别采集项目目录、启动命令和运行依赖。
2. 检查必要配置与环境文件是否齐备。
3. 启动项目并观察标准输出、错误输出和中间产物。
4. 使用 MCP 工具检查运行结果或相关页面状态。
5. 根据证据判断假设成立与否。

## 证据记录

- `python -m ozon_pipeline.cli show-browser-config` 执行成功，CLI、依赖和浏览器配置解析正常。
- 当前解析出的浏览器配置为 `cdp_url=http://127.0.0.1:9222`，并非 README 中调度脚本说明里的 `9223`。
- `python -m ozon_pipeline.cli launch-real-chrome --url https://www.ozon.ru/` 已成功拉起真实 Chrome。
- `http://127.0.0.1:9222/json/version` 可访问，说明 CDP 端口 `9222` 已就绪。
- `http://127.0.0.1:9222/json/list` 返回中可见 Ozon 页面与插件/毛子相关上下文。
- 使用 MCP 浏览器查看 `http://127.0.0.1:9222/json/list`，可见：
  - `https://ozon.maozierp.com/#/selection/top-list`
  - `https://www.ozon.ru/?__rr=1&abt_att=1`
- 执行 `python -u -m ozon_pipeline.cli fetch-sku 1608725864` 成功退出，输出：
  - `skipped sku: 1608725864 未命中规则: 0325 优质品; 品牌不是无品牌(Starfit); 月销量>65; 上架天数>180; 退货取消率缺失; 跟卖人数缺失`
- 查询 MySQL `sku_universe` 表，已存在该 SKU 最新记录，示例字段：
  - `sku=1608725864`
  - `brand=Starfit`
  - `sold_count=1187`
  - `last_seen_at=2026-05-19 01:35:26`
  - `formal_rule_reason=未命中规则: 0325 优质品; 品牌不是无品牌(Starfit); 月销量>65; 上架天数>180; 退货取消率缺失; 跟卖人数缺失`
- 执行默认生产入口 `scripts/restart_and_expand.ps1`（短等待验证）失败，错误为：
  - 脚本检测 `9223` 未就绪
  - 5 秒内未等到 `9223`，直接抛错退出
  - 与当前实际工作浏览器端口 `9222` 不一致
- 执行 GUI 入口 `run_gui.bat` 后，GUI 进程保持运行，说明当前入口界面已成功启动。
- 由于 GUI 本身只是控制面板，实际点击“开始采集”会调用 `expand-seller-backlog` / `crawl-top-list-network` / `expand-seed-pool-network` 之一；当前 GUI 默认推荐模式是 `expand-seller-backlog`。
- 按 GUI 默认生产策略启动：
  - `python -u -m ozon_pipeline.cli expand-seller-backlog --process-limit 100 --max-depth -1 --max-sellers 0 --seller-sku-workers 3`
  - 启动后输出：`browser mode: attach_existing_cdp=http://127.0.0.1:9222`
  - 随后输出：`expand-network round 1 due: fetched=100 process_limit=100`
  - 随后输出：`crawl seller: https://www.ozon.ru/seller/kruglosutochnyy-magazin-bytovoy-tehniki-2th/ | depth= 0 | source= entrypoint_api_full | items= 326 | crawl= 39.5s`
- 生产采集运行期间的数据库观测：
  - 初始观测：`seller_due_rows=29981`，`sku_universe=317007`
  - 随后观测到 `recent_universe_10m` 从 `0` 增长到 `3`
  - 再次观测到 `recent_universe_10m` 继续增长到 `10`
  - 最新 `sku_universe` 行更新时间推进到 `2026-05-19 01:51:06`
  - 最新 SKU 示例：`4281743517`

## 当前状态

- 单 SKU 采集链路已验证可采到数据并落库。
- 当前默认生产入口脚本未能正常跑通，已确认存在 `9223` 与 `9222` 的端口不一致问题。
- GUI 入口已成功启动。
- 按 GUI 当前默认策略的生产采集已启动并持续运行中，已确认开始消费卖家 backlog，并持续写入 `sku_universe`。
