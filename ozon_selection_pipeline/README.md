# Ozon 选品采集管道

这是当前项目的干净版说明文档，只描述**现在仍在使用**的结构、命令和数据流，不再保留历史试验路径。

## 项目目标

这个项目用于把 Ozon 选品相关数据沉淀到 MySQL，核心目标有两类：

1. 获取毛子 ERP / 插件提供的结构化 SKU 数据。
2. 通过跟卖卖家和卖家主页扩展 SKU 池，形成可持续增长的选品库。

当前项目已经把“全量发现”和“正式筛选”分成了两层：

- **全量存储层**
  任何来源发现到的 SKU 先进入总库，先存再筛。
- **正式筛选层**
  只有严格命中规则的 SKU 才会被标记为正式 SKU，并继续承担卖家扩展任务。

---

## 当前架构

当前真实在跑的链路只有两条：

### 1. 榜单种子扩展链路

`毛子榜单页 -> top_list_skus -> seed_pool_skus -> 轻规则筛选 -> 跟卖列表 -> 卖家主页 -> 严规则入库`

特点：

- 榜单数据先缓存。
- 种子 SKU 先进入独立种子表。
- 种子层只做“扩展卖家入口”的轻规则。
- 卖家主页发现的 SKU 再走严格入库规则。

### 2. 单 SKU 校验链路

`SKU -> 毛子 sku3 -> 商品页信息 -> 插件卡片补字段 -> 跟卖列表 -> 严规则入库`

特点：

- 用于验证某个 SKU 是否符合最终选品规则。
- 命中规则后才写正式产品表、指标表、跟卖卖家表。

### 3. 默认生产调度链路

当前默认生产不是每次启动都先刷种子，而是按 backlog 水位调度：

`restart_and_expand.ps1 -> 读取 seed_pool / seller_shops 水位 -> 卖家 backlog 优先 -> backlog 低于阈值时再处理种子池`

默认口径：

- 如果 `seller_shops` 中待采卖家数 `>= 1000`，优先跑卖家 backlog。
- 只有当待采卖家数 `< 1000` 时，才回到 `seed_pool_skus` 继续处理种子。
- `deferred` 状态的种子默认暂停自动重试，不再抢占生产资源。

这套策略的目标不是“让每个模块都轮到”，而是尽量把浏览器时间优先让给高收益的卖家页扩库。

---

## 目录结构

```text
ozon_selection_pipeline/
├─ .env.example
├─ .gitignore
├─ README.md
├─ requirements.txt
├─ scripts/
│  └─ run_top_list_seed_flow.ps1
├─ sql/
│  ├─ 001_init.sql
│  ├─ 002_seed_pool.sql
│  └─ 003_sku_universe.sql
└─ ozon_pipeline/
   ├─ __init__.py
   ├─ browser_ozon.py
   ├─ cli.py
   ├─ config.py
   ├─ db.py
   ├─ maozi_api.py
   ├─ ozon_frontend.py
   ├─ repository.py
   ├─ rules.py
   └─ util.py
```

---

## 模块职责

### `config.py`

统一读取 `.env`，集中管理数据库、毛子接口、浏览器、代理和榜单缓存参数。

### `db.py`

只做最基础的 MySQL 连接、查询、执行、运行 SQL 文件。

### `repository.py`

负责所有入库和状态更新逻辑，是数据库写入的唯一业务入口。

### `maozi_api.py`

负责直接请求毛子 ERP 接口。

### `ozon_frontend.py`

负责解析 Ozon 前端接口返回结构，主要用于：

- 跟卖列表
- 卖家主页商品列表

### `browser_ozon.py`

负责通过 Playwright / CDP 复用真实浏览器上下文：

- 调 Ozon 同源接口
- 调毛子榜单页接口
- 调插件扩展页 `sku3`
- 采商品页和插件卡片字段
- 自动回收无用 `about:blank / popup / chrome-error` 页面，避免浏览器卡死

### `rules.py`

只负责规则定义与规则判断，不直接做任何网络请求和数据库写入。

### `cli.py`

命令入口，负责把各个模块串起来。

这里也是当前不同采集策略的主落点：

- `cmd_expand_seed_pool_network`
  种子池扩展策略
- `cmd_expand_seller_backlog`
  卖家 backlog 扩展策略
- `cmd_crawl_top_list_network`
  榜单抓取 + 种子入池策略
- `run_seller_network`
  卖家主页递归扩展的核心执行器

### `scripts/restart_and_expand.ps1`

当前默认生产入口脚本。

负责：

- 检查 `9223` CDP 浏览器是否可用
- 输出当前数据库水位
- 按“卖家 backlog 优先，种子池次之”的策略自动调度
- 在同一个 PowerShell 窗口里循环执行，直到没有可处理 backlog

---

## 当前数据库分层

### 1. 总库层

- `sku_universe`
  所有来源 SKU 的最新总快照表，也是当前最重要的一张表。
  存每个 SKU 的最新标题、品牌、类目、价格、主图、Maozi `sku3` 扁平字段、跟卖人数、正式规则命中结果、种子规则命中结果、原始 JSON、中文字段映射 JSON 和最近采集时间。
- `sku_discovery_sources`
  SKU 来源轨迹表。
  记录每个 SKU 是从哪里被发现的，例如来自榜单、某个卖家主页、某个 SKU 的跟卖列表、后续其他页面来源。

### 2. 正式入库层

- `seed_skus`
  严格规则检查状态表。
  存这个 SKU 最近一次正式规则检查后的状态，例如 `qualified`、`rejected`、`failed`、`pending`，以及拒绝原因、最近检查时间。
- `sku_products`
  商品主档表。
  只有严格命中正式规则的 SKU 才会进入这张表。
  这里保存正式 SKU 的商品主档，以及和 Maozi `sku3` 对齐后的扁平字段、中文字段映射 JSON。
- `sku_plugin_metrics`
  正式指标信息表。
  只有严格命中正式规则的 SKU 才会稳定落在这里，主要存 Maozi / 插件指标，比如月销量、月销售额、广告费占比、退货取消率、重量、上架天数等。

### 3. 卖家网络层

- `seller_shops`
  卖家主表。
  存卖家主页链接、卖家名、最近采集时间、下次允许采集时间，是 30 天卖家屏蔽的基础表。
- `seller_offers`
  跟卖关系表。
  存“某个源 SKU 的跟卖卖家列表”，也就是 `source_sku -> seller` 的关系，以及跟卖价格、跟卖商品链接、图片、原始 JSON。
- `seller_home_skus`
  卖家主页发现表。
  存“某个卖家主页下有哪些 SKU”，以及该卡片上的标题、价格、主图、原始卡片 JSON。

### 4. 榜单种子层

- `top_list_runs`
  榜单抓取运行表。
  记录每一轮榜单请求的查询参数、页码范围、抓了多少页、多少条、处理了多少种子。
- `top_list_skus`
  榜单快照缓存表。
  保存每轮榜单返回的原始 SKU 快照，偏缓存 / 审计，不直接承担扩库状态。
- `seed_pool_skus`
  种子工作表。
  当前真正用于扩库的榜单种子池，存种子 SKU 及其处理状态、最近处理原因、最近一次跟卖人数、最近处理快照哈希。

### 5. 预留任务层

- `crawl_tasks`
  任务队列表，预留给常驻 worker 用，例如 seller_home、seller_offers、refresh 任务。
- `crawl_logs`
  任务日志表，预留给后续 worker 记录执行日志和错误。

当前 `crawl_tasks` 和 `crawl_logs` 仍然保留，但还没有正式启用常驻 worker。

---

## 规则体系

### 1. 正式入库规则

规则名：`0325 优质品`

当前口径：

- 无品牌
- 月销量 `1 ~ 65`
- 价格 `20 ~ 800 CNY`
- 重量 `<= 5000g`
- 上架天数 `<= 180`
- 发货模式包含 `FBS`
- 退货取消率 `<= 5`
- 跟卖人数 `<= 25`

只有命中这套规则的 SKU，才会写入：

- `seed_skus`
- `sku_products`
- `sku_plugin_metrics`
- `seller_offers`

### 2. 榜单种子扩展规则

规则名：`榜单种子扩展`

当前口径：

- 月销量 `1 ~ 65`
- 重量 `<= 5000g`
- 发货模式包含 `FBS`
- 退货取消率 `<= 5`
- 跟卖人数 `<= 50`

种子层**不再拦截**：

- 品牌
- 价格
- 上架时间

这层的目的不是正式入库，而是尽量找到更多可扩展的卖家入口。

---

## 当前入库原则

现在的主原则是：

- 所有发现到的 SKU 先写 `sku_universe`
- 所有发现来源写 `sku_discovery_sources`
- 跟卖关系继续写 `seller_offers`
- 卖家主页发现继续写 `seller_home_skus`
- 所有跑过 Maozi `sku3` 的字段，都尽量同步展开到 `sku_universe`
- 严格命中正式规则的 SKU，继续写 `seed_skus / sku_products / sku_plugin_metrics`

也就是说，规则不再控制“能不能存”，而是控制：

- 这个 SKU 是否算正式 SKU
- 这个 SKU 是否继续扩展卖家网络

另外，当前 `sku3` 的落库有两层表达：

- 英文结构化列：方便程序规则、SQL 条件筛选、统计计算
- 中文字段映射 JSON：尽量贴近毛子插件页面口径，便于人工查看和快速核对

---

## 配置说明

复制模板：

```powershell
copy .env.example .env
```

关键配置如下：

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DB_NAME=ozon_selection

MAOZI_TOKEN=replace-with-your-token

CHROME_PROFILE_DIR=profiles/profile-001
CHROME_EXTENSION_DIR=../maozi-plugin-2.3.2
CHROME_CHANNEL=chrome
CHROME_PROXY_SERVER=
CHROME_CDP_URL=
CHROME_REMOTE_DEBUGGING_PORT=9222
CHROME_HEADLESS=false
CHROME_LAUNCH_DISPLAY=:1
CHROME_LAUNCH_XAUTHORITY=/run/user/1000/gdm/Xauthority
CHROME_FINGERPRINT_MASK_ENABLED=true
CHROME_FINGERPRINT_USER_AGENT=
CHROME_FINGERPRINT_PLATFORM=Win32
CHROME_FINGERPRINT_PLATFORM_LABEL=Windows
CHROME_FINGERPRINT_HARDWARE_CONCURRENCY=16
CHROME_FINGERPRINT_DEVICE_MEMORY=8
CHROME_FINGERPRINT_LOCALE=zh-CN
CHROME_FINGERPRINT_TIMEZONE=
CHROME_FINGERPRINT_SCREEN_WIDTH=1920
CHROME_FINGERPRINT_SCREEN_HEIGHT=1080
CHROME_FINGERPRINT_SCREEN_AVAIL_WIDTH=0
CHROME_FINGERPRINT_SCREEN_AVAIL_HEIGHT=0
CHROME_FINGERPRINT_COLOR_DEPTH=24
CHROME_FINGERPRINT_WEBGL_VENDOR=Google Inc. (NVIDIA)
CHROME_FINGERPRINT_WEBGL_RENDERER=ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)

RUB_TO_CNY_RATE=0.0912

TOP_LIST_REFRESH_HOURS=24
TOP_LIST_RECHECK_QUALIFIED_DAYS=7
TOP_LIST_RECHECK_REJECTED_DAYS=21
TOP_LIST_RECHECK_FAILED_HOURS=12
TOP_LIST_RECHECK_CHANGED_HOURS=72
TOP_LIST_DEFAULT_SALES_MIN=1
TOP_LIST_DEFAULT_SALES_MAX=65
TOP_LIST_DEFAULT_PAGE_SIZE=50
TOP_LIST_DEFAULT_MAX_PAGES=100
TOP_LIST_DEFAULT_CREATE_DATE_FROM=2025-04-01
TOP_LIST_DEFAULT_CREATE_DATE_TO=
```

说明：

- `RUB_TO_CNY_RATE`
  用来把榜单价格和 Ozon 商品页价格从卢布换算成人民币。
- `CHROME_CDP_URL`
  如果你已经手动打开了真实浏览器，可直接附着到现有浏览器会话。
- `CHROME_FINGERPRINT_MASK_ENABLED`
  开启后，`launch-real-chrome` 会先起 `about:blank`，再通过 CDP 注入 Windows 指纹并跳转目标页。
- `CHROME_LAUNCH_DISPLAY` / `CHROME_LAUNCH_XAUTHORITY`
  物理服务器上跑图形 Chrome 时使用；Ubuntu/GDM 常见组合是 `:1` 和 `/run/user/1000/gdm/Xauthority`。
- `CHROME_FINGERPRINT_*`
  控制 `User-Agent`、`platform`、`hardwareConcurrency`、`deviceMemory`、`WebGL`、`screen` 等伪装值。
- `TOP_LIST_DEFAULT_CREATE_DATE_TO`
  留空时，CLI 会自动使用当天日期。

---

## 安装与初始化

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

初始化数据库：

```powershell
python -m ozon_pipeline.cli migrate
```

`migrate` 会按顺序执行 `sql/` 目录下的全部 SQL 文件。

---

## 当前保留的 CLI 命令

### `migrate`

执行全部数据库迁移脚本。

```powershell
python -m ozon_pipeline.cli migrate
```

### `import-seeds`

导入手工种子 SKU。

```powershell
python -m ozon_pipeline.cli import-seeds seeds.txt
```

### `repair-seed-pool-failures`

修复 `seed_pool_skus` 里历史遗留的失败状态，避免旧的基础设施失败影响重试。  
默认只重置“`failed to fetch maozi sku3 for top-list sku ...`”这一类毛子取数失败。

```powershell
python -m ozon_pipeline.cli repair-seed-pool-failures
```

如果你确实要重置全部 `failed` 状态，再加：

```powershell
python -m ozon_pipeline.cli repair-seed-pool-failures --all-failed
```

### `fetch-sku`

对单个 SKU 执行正式规则校验并入库。

```powershell
python -m ozon_pipeline.cli fetch-sku 1608725864
```

### `fetch-offers`

抓单个 SKU 的跟卖列表并写入 `seller_offers`。  
这个命令现在已经统一了浏览器 / 直连逻辑，不再区分 `fetch-offers-browser`。

```powershell
python -m ozon_pipeline.cli fetch-offers 1608725864
```

### `fetch-seller-home`

优先通过向 `ozon.ru` 页面注入同源 `fetch` 抓卖家主页全量商品链接，不继续做规则判断。  
如果接口失败，才回退到旧的 DOM / 滚动兜底方案。

```powershell
python -m ozon_pipeline.cli fetch-seller-home https://www.ozon.ru/seller/daluwei-001-4118093/
```

### `crawl-seller-network`

从一个卖家主页开始，递归扩展到下一层跟卖卖家主页。  
如果只想做单卖家调试，可以用：

```powershell
python -m ozon_pipeline.cli crawl-seller-network https://www.ozon.ru/seller/daluwei-001-4118093/ --max-depth 0 --max-sellers 1
```

正式用法示例：

```powershell
python -m ozon_pipeline.cli crawl-seller-network https://www.ozon.ru/seller/daluwei-001-4118093/ --max-depth 1 --max-sellers 20
```

### `crawl-top-list-network`

当前主入口。  
从毛子榜单页拉 5000 条种子，进入 `seed_pool_skus`，再按轻规则扩展卖家网络。

```powershell
python -m ozon_pipeline.cli crawl-top-list-network --page-to 100
```

常见参数：

- `--skip-process`
  只刷新 / 复用榜单缓存，不处理种子。
- `--process-limit`
  只处理部分到期种子，适合调试。
- `--max-depth`
  卖家主页扩展深度。
- `--max-sellers`
  本轮最多处理多少个卖家主页。
- `--retry-deferred-now`
  手工强制把 `deferred` 种子重新拉回处理队列。默认不建议在生产中开启。

### `expand-seed-pool-network`

不重新拉榜单，直接基于当前 `seed_pool_skus` 做正式扩充。  
这个命令适合你现在这种场景：种子库已经准备好，只想从当前种子库继续扩展，直到卖家队列耗尽。

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network
```

默认行为：

- 直接扫描当前 `seed_pool_skus`
- 只处理到期 / 未处理的种子
- 如果配置了 `CHROME_CDP_URL`，命令会附着到你手动打开的浏览器，不会再弹一个新窗口
- 开跑前会先做一次 `sku3` 自检；如果浏览器登录态不完整，终端会停住等待你人工登录，按回车后继续重试
- 对重复出现在多个榜单快照中的同一 `sku` 自动复用本轮结果，避免重复请求
- 对种子命中的 SKU 抓跟卖卖家
- 从这些跟卖卖家进入卖家主页
- 卖家主页中的 SKU 继续走严格正式入库规则
- 继续递归到新的跟卖卖家，直到队列耗尽
- `deferred` 种子默认不再自动到期重试

常见参数：

- `--query-key`
  只处理某一个榜单快照对应的种子。
- `--process-limit`
  只处理部分到期种子，适合调试。
- `--retry-failed-now`
  立即重试上一次 `failed` 的种子，不必等待默认的失败重试冷却时间。
- `--retry-deferred-now`
  手工强制重试 `deferred` 的种子。默认关闭，生产调度不会自动开启。
- `--seed-sku-workers`
  同时处理多少个种子 SKU。默认 `4`。
- `--seller-sku-workers`
  同时处理多少个卖家主页 SKU。默认 `3`。
- `--max-depth -1`
  无限递归卖家网络，直到没有新卖家可扩展。
- `--max-sellers 0`
  不限制本轮卖家处理数量。

建议在大规模跑批时分批推进，例如：

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network --process-limit 200 --max-depth 1 --max-sellers 50 --seed-sku-workers 4 --seller-sku-workers 3
```

这样比一上来无限递归更稳，更容易观察浏览器和数据库压力。

如果你的机器是 `32G` 内存，并且毛子账号只允许单设备登录，推荐先从这组参数开始：

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network --process-limit 300 --max-depth 1 --max-sellers 80 --seed-sku-workers 4 --seller-sku-workers 3
```

更激进一点可以试：

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network --process-limit 500 --max-depth 1 --max-sellers 120 --seed-sku-workers 6 --seller-sku-workers 4
```

建议不要一上来就无限递归加高并发，先观察 10 到 20 分钟内的浏览器内存、失败率和卖家扩展速度。

### `expand-seller-backlog`

直接从 `seller_shops` 中读取“尚未采集 / 到期可重采”的卖家主页，优先消化卖家 backlog。

```powershell
python -m ozon_pipeline.cli expand-seller-backlog --process-limit 100 --max-depth -1 --max-sellers 0 --seller-sku-workers 1
```

这个命令适合当前生产阶段：

- 种子池已经提供了足够多的卖家入口
- `seller_shops` backlog 很大
- 想把浏览器时间优先让给卖家主页扩库
- 不希望 `deferred` 种子反复抢资源

默认行为：

- 从 `seller_shops` 中读取待采卖家
- 按 `last_collected_at / next_collect_after` 和 30 天规则决定是否该采
- 卖家页优先抓全量 SKU
- 卖家主页发现的 SKU 再走正式规则
- 合格 SKU 继续扩出下一层卖家

### `show-browser-config`

打印当前浏览器配置解析结果。

```powershell
python -m ozon_pipeline.cli show-browser-config
```

### `launch-real-chrome`

启动真实 Chrome / Chrome for Testing，保留登录态并支持 CDP 附着。  
如果开启了 `CHROME_FINGERPRINT_MASK_ENABLED=true`，它会先打开 `about:blank`，再用 CDP 注入伪装后导航到目标页。
这是当前唯一保留的“浏览器预热”命令。

```powershell
python -m ozon_pipeline.cli launch-real-chrome --url https://www.ozon.ru/
```

如果你已经自己手动启动了带 `9223` 之类远程调试端口的浏览器，后续采集命令只会附着到那个现有浏览器，不会再额外打开新窗口。

---

## PowerShell 包装脚本

项目保留了两个常用入口脚本：

[run_top_list_seed_flow.ps1](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/scripts/run_top_list_seed_flow.ps1)
[restart_and_expand.ps1](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/scripts/restart_and_expand.ps1)

### `run_top_list_seed_flow.ps1`

作用：

- 自动拼接榜单参数
- 自动把 `create_date_to` 设成当天
- 方便日常跑批

用法：

```powershell
.\scripts\run_top_list_seed_flow.ps1
```

### `restart_and_expand.ps1`

这是当前推荐的默认生产入口。

默认路径：

1. 连接现有 `9223` 浏览器
2. 读取 `seed_pool_skus` 和 `seller_shops` 当前 backlog
3. 如果 `seller_due_rows >= 1000`，优先跑 `expand-seller-backlog`
4. 如果 `seller_due_rows < 1000`，再跑 `expand-seed-pool-network`
5. 循环到种子和卖家 backlog 都跑空，或连续两轮无进展自动停下

默认启动方法：

```powershell
cd C:\project\maozi-plugin-2.3.2\ozon_selection_pipeline
powershell -ExecutionPolicy Bypass -File .\scripts\restart_and_expand.ps1 -SeedSkuWorkers 1 -SellerSkuWorkers 1 -SellerBacklogSeedThreshold 1000
```

你也可以直接在现有 PowerShell 里运行：

```powershell
.\scripts\restart_and_expand.ps1 -SeedSkuWorkers 1 -SellerSkuWorkers 1 -SellerBacklogSeedThreshold 1000
```

---

## 当前推荐工作流

### 1. 首次准备

1. 配好 `.env`
2. 跑 `migrate`
3. 用 `launch-real-chrome` 打开真实浏览器
4. 在浏览器里确认 Ozon 和插件都已登录
5. 用 `show-browser-config` 检查配置是否指向正确 profile

### 2. 日常扩展

1. 先确认 `9223` 浏览器已打开，并且 Ozon / 毛子登录态正常。
2. 默认直接运行 `restart_and_expand.ps1`。
3. 调度脚本会优先看卖家 backlog。
4. 如果卖家 backlog 足够大，就先跑 `expand-seller-backlog`。
5. 只有卖家 backlog 低于阈值时，才会继续消费 `seed_pool_skus`。
6. 由卖家主页发现的新 SKU 继续进入严格规则校验。

也就是说，当前推荐顺序不是“每次启动都先跑种子”，而是：

`先扩卖家，后补种子；种子负责开源，卖家负责放大`

如果你明确需要补充新方向，再手动运行榜单刷新：

```powershell
python -m ozon_pipeline.cli crawl-top-list-network --page-to 100 --skip-process
```

如果只是已有种子池需要继续扩展，而卖家 backlog 又不大，可以手动运行：

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network
```

### 3. 单 SKU 验证

1. 运行 `fetch-sku <sku>`
2. 查看 `seed_skus / sku_products / sku_plugin_metrics`
3. 如需单独检查跟卖列表，再运行 `fetch-offers <sku>`

---

## 重要实现细节

### 跟卖列表不再无差别抓取

当前策略是：

- 正式 SKU 流程里，只有“其它条件都接近命中、但缺少跟卖人数”时，才补抓 offers。
- 榜单种子流程里，也只有通过轻规则的种子 SKU，才继续抓完整跟卖列表。

### 卖家主页当前优先抓全量 SKU

当前实现已经升级为：

- 优先在 `ozon.ru` 页面上下文里注入脚本
- 请求卖家主页背后的 `entrypoint-api`
- 从 `widgetStates.infiniteVirtualPaginator.nextPage` 递归跟后续页
- 自动拿到卖家主页可见的全量 SKU
- 只有接口失败时，才回退到旧的 DOM / 滚动兜底方案

已经验证过的卖家主页样本：

- `https://www.ozon.ru/seller/daluwei-001-4118093/`：`18` 页，约 `130` 个 SKU
- `https://www.ozon.ru/seller/master-builder/`：`37` 页，约 `287` 个 SKU
- `https://www.ozon.ru/seller/lego-planeta/`：`18` 页，约 `135` 个 SKU

### 当前已做的浏览器性能优化

当前 CLI 在一次命令执行过程中会复用同一个浏览器会话：

- 不再为每个 `sku3 / seller_offers / seller_home` 请求重复附着 `CDP`
- 不再为每个请求重复创建和遗留临时页
- 对手动打开的 `9223` 浏览器，只附着复用，不额外弹新窗口
- 会自动清理无用的 `about:blank / popup / chrome-error` 页面

这对内存和速度都有帮助，尤其是长时间跑批时更明显。

### 单设备登录约束下的并行方式

毛子账号如果只支持单设备登录，当前项目的并行策略要遵守这条约束：

- 只保留一个你手动打开并已登录的真实浏览器
- 所有 worker 都附着到同一个 `CHROME_CDP_URL`
- 不额外启动第二个已登录毛子浏览器
- 不用多个机器同时登录同一个毛子账号

也就是说，当前的并行是“同一设备上的多 worker 附着同一浏览器”，不是“多个设备分别登录同一个账号”。

### 当前默认调度优先级

当前默认生产调度不是“有种子就先跑种子”，而是：

1. 如果卖家 backlog 足够大，优先处理卖家主页
2. 只有卖家 backlog 低于阈值，才回头继续处理种子池
3. `deferred` 种子默认暂停自动重试

这样做的原因是：

- 卖家页通常能一次性带出几十到几百个 SKU，单位浏览器时间收益更高
- 种子池更适合作为“入口补货”，而不是在 backlog 巨大时持续抢资源
- `deferred` 说明毛子字段仍在刷新，短期内重复撞它的边际收益低

如果确实要人工恢复 `deferred`，再显式加：

```powershell
python -m ozon_pipeline.cli expand-seed-pool-network --retry-deferred-now
```

### 卖家页默认只走批量，不再逐个补刀

卖家页 SKU 的毛子 sku3 数据现在只依赖批量预取（`prefetch_top_list_maozi_batch`），不再为少量漏网 SKU 单独发起毛子请求或插件卡片补救：

- 批量预取未覆盖的 SKU 直接跳过，写入 `sku_universe` 标记但不再进入规则判定
- 批量预取成功的 SKU 继续走规则判定和跟卖抓取，但跳过单 SKU 插件卡片补救
- 跟卖列表（seller offers）抓取仍然保留，因为它是规则判定的必要输入
- 卖家汇总中新增 `skipped` 计数器，与 `qualified / rejected / deferred` 并列

这样做的原因是：如果批量能获取 95%，剩下的 5% 要消耗同等时间逐个补抓，边际收益极低。把浏览器时间留给下一个卖家页的全量 SKU 更划算。

### 卖家 30 天屏蔽仍然生效

对通过种子扩展得到的下一跳卖家：

- 如果 30 天内采过，会自动跳过
- 根卖家入口不会被这条规则拦住

### 毛子 `sku3` 基础设施失败现在会立即允许重试

对于 `seed_pool_skus` 里这类失败：

- `failed to fetch maozi sku3 for top-list sku ...`

系统现在会把它当成“浏览器 / 登录态 / 取数上下文失败”，默认视为可立即重试，不再强制卡住默认的失败冷却时间。  
如果库里已经积累了旧失败状态，可以手动运行 `repair-seed-pool-failures` 做一次清理。

### 不同采集策略的代码位置

如果后面需要继续调策略，优先看这些位置：

- 默认生产调度脚本
  [restart_and_expand.ps1](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/scripts/restart_and_expand.ps1)
- 榜单抓取与种子入池
  [cli.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/cli.py)
  `cmd_crawl_top_list_network`
- 种子池扩展
  [cli.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/cli.py)
  `cmd_expand_seed_pool_network`
- 卖家 backlog 扩展
  [cli.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/cli.py)
  `cmd_expand_seller_backlog`
- 卖家页递归处理核心
  [cli.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/cli.py)
  `run_seller_network`
- 卖家页全量 SKU 抓取
  [browser_ozon.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/browser_ozon.py)
  `_fetch_seller_home_products_api`
- 种子批量 `sku3`
  [browser_ozon.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/browser_ozon.py)
  `top_list_sku3_batch / _fetch_top_list_sku3_batch`
- `deferred / failed / ttl` 判定
  [repository.py](/C:/project/maozi-plugin-2.3.2/ozon_selection_pipeline/ozon_pipeline/repository.py)
  `seed_pool_sku_due_state`

---

## 当前局限

这些问题当前仍然存在，但它们已经和“结构混乱”无关，属于真实功能边界：

- 没有常驻 worker
- `crawl_tasks` 只是预留，还没有消费器
- 没有并发 worker 调度
- 插件登录态仍然依赖浏览器 profile，不是纯后端无状态运行

---

## 建议的后续开发顺序

如果继续开发，优先顺序建议如下：

1. 给 `seed_pool_skus` 增加常驻 worker 消费
2. 做并发 seller / sku 处理
3. 给 seller_home 扩展增加正式调度
4. 增加统计报表命令，专门分析每层的淘汰比例
5. 视情况再决定是否加入分页和代理池

---

## 最后说明

当前仓库已经做过一轮结构级清理，原则如下：

- 删除历史试验入口
- 删除重复 CLI 命令
- 保留当前真实在跑的种子层 / 正式层双层架构
- README 只描述现在的系统，不再兼容历史文档口径

如果你后面看到代码和这份 README 不一致，以代码为准，再继续收敛即可。
