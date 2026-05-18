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

## 性能瓶颈分析与优化记录 (2026-05-19)

### 观察到的症状
- 处理包含 800 个 SKU 的大卖家时，耗时显著。
- 日志显示 `crawl seller` 仅需 75s，但随后的处理阶段在 6 分钟内未产出 summary。
- 卖家 SKU 准备阶段（upsert 列表）在大样本下非常缓慢（曾观测到 377 SKU 耗时 174s）。

### 核心原因分析
1. **频繁的同步数据库 IO**：原流程中每个 SKU 处理都会触发 3-4 次同步数据库 `INSERT/UPDATE`（`sku_universe`, `sku_plugin_metrics`, `sku_products`, `seed_pool_skus`）。对于 800 SKU 的卖家，这意味着约 3000 次 DB 往返，由于数据库通常处于穿透或公网环境，延迟被极度放大。
2. **并发瓶颈**：默认 `seller_sku_workers=3` 且受限于浏览器全局锁。即使在 `batch_only_mode` 下不需要真实浏览器操作，低并发数也限制了 DB 吞吐。
3. **冗余逻辑**：`process_sku` 内部存在多次冗余的规则判定和 DB 写入。

### 实施的优化方案
1. **全链路批量写库**：
   - 在 [repository.py](file:///c:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/repository.py) 中新增 `bulk_upsert_sku_results` 和 `bulk_upsert_sku_universe_full`。
   - 将零散的单条 SQL 更新合并为 `execute_many` 批量操作，大幅减少网络往返次数。
2. **处理流程瘦身**：
   - 修改 [cli.py](file:///c:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/cli.py) 的 `process_sku`，支持 `skip_db=True` 模式，在批量处理时不执行任何单条写库，仅返回数据结构。
3. **大幅提升并发度**：
   - 默认 `seller_sku_workers` 从 3 提升至 6。
   - 在 `run_seller_network` 的批量模式下，worker 数量临时提升至原配置的 3 倍（最高 16），充分利用 DB 吞吐。
4. **批量预取参数调优**：
   - 修改 [config.py](file:///c:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/config.py)，将 SKU3 批量大小从 40 提升至 60，并发从 12 提升至 16，批次延迟从 500ms 降至 100ms。

### 预期效果
- 800 SKU 卖家的处理时长预计从 10 分钟以上缩短至 3-5 分钟以内。
- 准备阶段（prepared items）的 DB 耗时应有量级下降。

## 当前状态
- 代码已完成性能优化并保存至 Git。
- GUI 已具备更强的观测能力。
- 准备重新启动生产采集进行验证。
