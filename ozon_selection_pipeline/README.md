# Ozon Selection Pipeline

毛子 ERP 辅助选品采集系统。通过 Playwright CDP 注入浏览器 JS，调用毛子 API 拉取榜单/商品/SKU3 数据，经多层规则筛选，自动扩展卖家网络，挖掘可跟卖的优质无品牌商品。

## 目录

- [快速开始](#快速开始)
- [四种采集模式](#四种采集模式)
- [错误纠正机制](#错误纠正机制)
- [代码分布](#代码分布)
- [数据库存储](#数据库存储)
- [配置说明](#配置说明)
- [版本迭代](#版本迭代)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置（填入你的数据库、Chrome 路径等）
cp .env.example .env

# 3. 启动 GUI（Windows 上推荐）
python -m ozon_pipeline.cli gui
# 或者双击 run_gui.bat

# 4. 命令行模式
python -m ozon_pipeline.cli crawl-top-list-network --cdp-url http://127.0.0.1:9222 --main-type hot --page-from 1 --page-to 20

# 5. 运行前自检（DB / 9222 浏览器 / profile / 插件 / 毛子登录态）
python -m ozon_pipeline.cli doctor --cdp-url http://127.0.0.1:9222

# 6. 无人值守长跑（选中的采集路径完成/空闲后继续下一轮）
python -m ozon_pipeline.cli expand-seller-backlog --cdp-url http://127.0.0.1:9222 --forever --idle-sleep-seconds 300
```

**前置条件**：Chrome 浏览器已打开并登录毛子 ERP（`https://ozon.maozierp.com`），远程调试端口 9222 已启用。

换设备或长跑前建议先执行 `doctor`。如果输出中存在 `FAIL`，先修复 DB、CDP、浏览器 profile、插件目录或毛子登录态，再启动采集。

---

## 四种采集模式

### 1. 卖家列表循环扩充（推荐日常使用）

```
seller_shops 表中取到期卖家（29211 个待处理队列）
  → 价格预筛（20-1000 CNY）
  → 品牌预检：爬 3 页（24 条），标题全含拉丁字母 → 永久冻结跳过
  → 全量爬取卖家主页商品
  → 批量获取毛子 SKU3 指标（60 条/批，10 并发）
  → DEFAULT_SELECTION_RULE 严格判定（8 项规则）
  → 合格品写入 sku_products，跟卖卖家入 BFS 队列
  → 递归扩展卖家网络（无限深度）
  → while True 循环直到无到期卖家
```

**适用**：日常持续消化卖家池，自动发现新卖家。

命令行一次性验收示例：

```bash
python -m ozon_pipeline.cli expand-seller-backlog --cdp-url http://127.0.0.1:9222 --process-limit 200 --max-sellers 200
```

命令行无人值守示例：

```bash
python -m ozon_pipeline.cli expand-seller-backlog --cdp-url http://127.0.0.1:9222 --process-limit 0 --max-sellers 0 --forever --idle-sleep-seconds 300
```

### 2. 榜单采集网络

```
逐页拉取榜单（1-100 页，50 条/页）
  → 每页即时处理：预筛 TOP_LIST_SEED_RULE（月销 3-200）→ SKU3 → 种子判定
  → 命中种子爬跟卖列表 → 卖家入队
  → 全部页完成后 → 自动切换到卖家列表循环消化
```

**适用**：周期性拉新榜单获取新鲜种子，然后自动进入卖家扩展。

命令行拉取 5000 条榜单种子示例：

```bash
python -m ozon_pipeline.cli crawl-top-list-network --cdp-url http://127.0.0.1:9222 --main-type hot --page-from 1 --page-to 100 --page-size 50 --skip-process
```

### 3. 种子池处理

```
从 seed_pool_skus 表取出到期种子（LIMIT 30000）
  → 预筛 TOP_LIST_SEED_RULE
  → SKU3 批量获取
  → 规则判定 → 卖家扩展
  → 支持强制重试失败/暂缓/淘汰项
```

**适用**：对已拉取的榜单种子重新判定（规则变更后），或处理之前失败的种子。

命令行处理历史种子示例：

```bash
python -m ozon_pipeline.cli expand-seed-pool-network --cdp-url http://127.0.0.1:9222 --source-type top_list --process-limit 0 --forever --idle-sleep-seconds 300
```

### 4. 多类目采集网络

```
遍历 ozon_categories 指定层级（L1/26 个、L2/393 个、L3/1429 个）
  → 每个类目构建 category1/category2/category3 过滤
  → 拉榜单页（每类目可配页数）
  → 种子处理 + 卖家扩展
```

**适用**：按类目维度精细化拉取，覆盖更多细分市场。

命令行示例：

```bash
python -m ozon_pipeline.cli multi-category-network --cdp-url http://127.0.0.1:9222 --category-level 2 --pages-per-category 5 --forever
```

---

## 错误纠正机制

### 统一登录恢复（unified_login_recovery）

Token 失效时自动触发，4 层递进：

```
1. 刷新 Ozon 页面 → 检测插件"请登录"按钮 → 点击处理
2. ensure_authenticated → 扫描所有页面处理登录/弹窗
3. 新建登录页 → auto_login（滑块 + 账号密码）
4. 全部失败 → 发送飞书通知人工介入
```

### Token 双重路径

| 路径 | 来源 | 用途 |
|------|------|------|
| Path A | `chrome.storage.local['maozierp-token']` | 插件写入（优先，3s 超时） |
| Path B | `localStorage['maozierp-core-access'].accessToken` | 网页写入（降级） |

### SKU3 限流处理

- 60 条/批，批次间 500ms 延迟
- HTTP 429 → 自动降速
- 连续 1 批全失败 → 熔断（circuit break），跳过剩余批次

### DB 断连重试

- 连接池 `pool_pre_ping=True` 探活
- 瞬态错误自动重试 3 次（0.5s/1s/1.5s 递增）
- 大包传输容忍：`read_timeout=600s, write_timeout=600s`

### 采集停滞自愈

- 连续失败 10 个卖家 → 暂停 60 秒后重试
- 连续 3 轮无进展 → 飞书通知并停止
- 认证失败自动恢复（最多 3 次），连续 5 次失败 → 彻底停止

---

## 代码分布

```
ozon_pipeline/
├── cli.py                # 核心业务：4种采集模式、种子处理、卖家网络BFS
├── gui.py                # Tkinter GUI控制台
├── browser_ozon.py       # Playwright CDP浏览器管理、JS注入、API调用
├── repository.py         # 数据库读写、批量操作、状态管理
├── rules.py              # 选品规则定义(DEFAULT/TSEED/TOP_LIST)
├── config.py             # 环境配置加载、DBProfile多Profile
├── db.py                 # MySQL连接池(SQLAlchemy+PyMySQL)
├── captcha_handler.py    # 滑块验证码、自动登录、插件弹窗、统一恢复
├── db_sync.py            # 表结构同步引擎(local→lx)
├── feishu.py             # 飞书自定义机器人通知
├── util.py               # 工具函数(价格解析/URL规范化/JSON序列化)
├── maozi_api.py          # 毛子API直接HTTP调用(备用)
├── ozon_frontend.py      # Ozon前端HTML解析
└── seed_categories.py    # 类目树维护

sql/                      # 数据库迁移脚本(建表/ALTER TABLE)
profiles/                 # Chrome用户配置(已gitignore)
.env                      # 环境配置(含3个DBProfile)
.env.lx                   # lx服务器配置
```

---

## 数据库存储

### 核心表（> 1 万行）

| 表 | 行数 | 用途 |
|----|------|------|
| `seed_skus` | 274k | 质量判定永久档案（sku + status + score + reason） |
| `sku_universe` | 327k | 全量商品档案（含 is_formal_qualified 判定标记） |
| `seller_home_skus` | 274k | 卖家主页暂存表（采集时临时写入，完成后清理） |
| `seller_offers` | 52k | 跟卖关系（合格 SKU → 跟卖卖家） |
| `seller_shops` | 36k | 卖家主表（含质量追踪字段） |

### 辅助表

| 表 | 行数 | 用途 |
|----|------|------|
| `seed_pool_skus` | 20k | 榜单种子池（含处理状态机+冷却逻辑） |
| `sku_products` | 1.6k | **合格品主档**（只存合格的，可直接售卖） |
| `sku_plugin_metrics` | 3k | 毛子 SKU3 原始指标全量存档 |
| `sku_discovery_sources` | - | SKU 来源轨迹 |
| `top_list_skus` | 15k | 榜单快照档案 |
| `top_list_runs` | - | 榜单采集运行记录 |
| `crawl_tasks` | - | 分布式任务队列 |
| `crawl_logs` | - | 采集日志 |
| `ozon_categories` | 1.8k | 类目树（多类目采集用） |

### 关键字段说明

**seller_shops 质量追踪**：
- `total_collect_attempts`：累计采集次数
- `last_qualified_count`：最近一次合格 SKU 数
- `next_collect_after`：下次采集时间（永久冻结设为 2099 年）
- 自动淘汰：3 次采集且达标数为 0 → 暂停 7 天

**sku_universe 判定标记**：
- `is_formal_qualified`：1=合格，0=不合格
- `formal_rule_name`：判定使用的规则名
- `formal_rule_reason`：不合格原因

### 三表关系

```
                  ┌─ qualified → sku_products（只有合格的）
种子/SKU 经过规则 ─┤
                  └─ 全量 → sku_plugin_metrics（全量指标）+ sku_universe（全量档案+标记）
```

### 优化要点

- `sku_products` 只存合格品（rejected 不写入）
- `product_raw_json` / `maozi_raw_json` 不再写入（省 1.2GB 存储）
- 批量 INSERT（`execute_insert_many`）替代逐条写入
- DB Profile 多配置切换：local / ts-lx / frp-lx

---

## 配置说明

### .env 关键配置

```bash
# 数据库（3个Profile）
DB_HOST=100.97.110.39         # 远程服务器
DB_HOST_LOCAL=127.0.0.1       # 本机
DB_HOST_FRP=127.0.0.1         # FRP隧道

# Chrome
CHROME_CDP_URL=http://127.0.0.1:9222
CHROME_PROFILE_DIR=profiles

# 并发控制
TOP_LIST_SKU3_BATCH_SIZE=100       # SKU3批量每批多少条
TOP_LIST_SKU3_BATCH_CONCURRENCY=10 # 批内并发数
SEED_POOL_QUERY_LIMIT=30000        # 种子池一次查多少条
SELLER_FAST_MODE=true              # 快速模式（缺数据跳过）
SELLER_WRITE_MINIMAL=true          # 极简写入（只写合格品）
SELLER_PAGE_TIMEOUT_SECONDS=120    # 异常卖家页最多等待秒数，超时跳过

# 写入超时
DB_READ_TIMEOUT=600
DB_WRITE_TIMEOUT=600

# 无人值守循环
COLLECTION_IDLE_SLEEP_SECONDS=300
COLLECTION_CYCLE_SLEEP_SECONDS=60
COLLECTION_ERROR_SLEEP_SECONDS=300

# 汇率
RUB_TO_CNY_RATE=0.0912
```

---

## 版本迭代

### v2.3.2 关键优化

| 日期 | 提交 | 说明 |
|------|------|------|
| 6/4 | `952463f` | 回退中文翻译，恢复稳定英文版 |
| 6/4 | `2c87fc2` | 修复：单 worker 跳过品牌浅爬避免串行卡住 |
| 6/3 | `e37b33d` | 新增 SELLER_WRITE_MINIMAL 极简写入模式 |
| 6/3 | `bb32c4c` | 修复：SKU3 Header 从 Client:plugin 改为 Client:pc（解决 FETCH_ERROR） |
| 6/3 | `d5cb92f` | 修复：_fetch_maozi_sku3 去掉插件弹窗 Path A（避免 120s 超时） |
| 6/3 | `99ddd46` | 优化：批量 mark_seed_pool_selected（7047 次→1 次） |
| 6/3 | `2bc1501` | 优化：批量 mark_seed_pool_processed |
| 6/3 | `a00ef4e` | 规则调整：入库价格 20-1000CNY |
| 6/2 | `7e8e9ee` | 优化：SKU3 Path A 去掉页面导航（避免 20s 浪费） |
| 6/2 | `c1598ec` | GUI 左右分栏布局 |
| 6/2 | `2dde849` | 新功能：品牌预判 + 浅爬 3 页（拉丁字母检测） |
| 6/1 | `f3f7293` | Token 双路径注释清楚 |
| 6/1 | `7fefeb5` | 统一登录恢复机制 + 飞书告警 |
| 6/1 | `a5d69af` | 规则放宽：月销量上限 65→200 |
| 6/1 | `d8d2b1c` | 优化：去 product_raw_json（省 1.2GB） |
| 6/1 | `865c7b7` | process_limit=0、max_depth=-1 不限量模式 |
| 5/31 | `8591911` | 批量写入、GUI 29 参数、登录流程 |
