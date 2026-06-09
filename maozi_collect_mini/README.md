# 毛子采集 Mini 版 — 各功能模块能力边界文档

## 项目概述

本系统是 `ozon_selection_pipeline` 的精简重构版，专注于 **Ozon 俄罗斯电商平台商品选品数据的自动化采集**。相比原项目，本版大幅简化了代码结构，保留了核心采集能力。

---

## 一、模块能力边界总览

```
┌────────────────────────────────────────────────────────────────┐
│                        gui.py (GUI界面)                        │
│  模式选择 → 参数配置 → 暂存config.json → 启动采集              │
└──────────────────────────────┬─────────────────────────────────┘
                               │ config.json
               ┌───────────────┼───────────────┐
               ▼                               ▼
┌──────────────────────────┐   ┌──────────────────────────┐
│  ranking_collector.py    │   │  seller_collector.py     │
│  (榜单采集)               │──▶│  (卖家主页采集)           │
└──────────┬───────────────┘   └──────────┬───────────────┘
           │                               │
           └───────────┬───────────────────┘
                       ▼
              ┌─────────────────┐
              │    main.py      │
              │  (编排器)        │
              └────────┬────────┘
                       ▼
   ┌──────────────────────────────────────────────────────┐
   │              数据持久化层                             │
   │  repository.py ◀── db.py ◀── MySQL                   │
   │  rules.py (筛选规则)                                  │
   └──────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────┐
   │              纠错与告警层                              │
   │  captcha_handler.py (滑块验证+自动登录+多级恢复)       │
   │  feishu.py (飞书Webhook告警)                          │
   └──────────────────────────────────────────────────────┘
```

---

## 二、各模块详细说明

### 2.1 `config.py` — 配置管理

**能力边界：**
- 加载 `settings.json` 配置文件（JSON 格式，便于直接编辑，替代原 `.env`）
- 提供 `Settings` 数据类（所有配置项的单例访问点）
- 读写 `config.json` 运行时配置（由GUI暂存）

**不负责：**
- 不验证配置合法性（由调用方各自校验）
- 不处理配置热更新（启动时一次性加载）

**配置项清单：**

| 分类 | 配置项 | 默认值 | 说明 |
|------|--------|--------|------|
| 数据库 | DB_HOST | 127.0.0.1 | MySQL主机地址 |
| 数据库 | DB_PORT | 3306 | MySQL端口 |
| 数据库 | DB_NAME | ozon_selection | 数据库名 |
| 数据库 | DB_READ_TIMEOUT | 600 | 读取超时(秒)，跨公网建议600 |
| 毛子API | MAOZI_TOKEN | - | 毛子ERP Token |
| 毛子API | MAOZI_BASE_URL | api.maozierp.com | API地址 |
| 浏览器 | CHROME_CDP_URL | 127.0.0.1:9222 | CDP调试地址 |
| 浏览器 | CHROME_PROFILE_DIR | profiles/profile-001 | 浏览器用户目录 |
| 业务 | RUB_TO_CNY_RATE | 0.0912 | 卢布兑人民币汇率 |

---

### 2.2 `db.py` — 数据库访问层

**能力边界：**
- 使用 SQLAlchemy + pymysql 连接 MySQL
- 连接池管理：`pool_size=10`, `pool_recycle=600s`, `pool_pre_ping=True`
- 自动重试瞬态错误（死锁1205、连接丢失2003/2006/2013），最多重试3次
- 提供 `execute()`, `fetch_one()`, `fetch_all()`, `execute_insert_many()` 等封装
- 支持 `%(name)s` 风格的参数占位符（自动转为 `:name`）
- 支持 SQL 文件批量迁移（`run_sql_file`）

**不负责：**
- 不管理表结构变更（需手动执行 `sql/init.sql` 或用 `task/seed_categories.py`）
- 不做ORM映射（纯SQL + 字典结果）

**防御性编程特性（遵守AGENTS.md规范）：**
- `pool_pre_ping=True`：每次从池中取连接前先探活
- `pool_recycle=600`：连接存活600秒后自动回收
- `read_timeout=600` / `write_timeout=600`：大包传输极限容忍
- 自动重试瞬态网络错误

---

### 2.3 `rules.py` — 筛选规则

**能力边界：**

#### 种子筛选规则（榜单种子扩展）
用于榜单采集后判定哪些商品应进入 `seed_pool_skus`。

| 条件 | 阈值 |
|------|------|
| 是否要求无品牌 | **不要求** |
| 月销量 | 3 ~ 200 件 |
| 重量 | ≤ 5000g |
| 上架天数 | ≤ 200 天 (SEED_CREATE_DAYS_MAX) |
| 退货取消率 | ≤ 5% |
| 跟卖人数 | ≤ 50 个 |
| 发货模式 | 必须包含 FBS |

#### 合格商品筛选规则（0325优质品）
用于最终判定SKU是否进入 `sku_products`。

| 条件 | 阈值 |
|------|------|
| 品牌 | **必须无品牌** |
| 月销量 | 1 ~ 65 件 |
| 价格(CNY) | 20 ~ 1000 元 |
| 重量 | ≤ 5000g |
| 上架天数 | ≤ 180 天 (DEFAULT_CREATE_DAYS_MAX) |
| 退货取消率 | ≤ 5% |
| 跟卖人数 | ≤ 25 个 |
| 发货模式 | 必须包含 FBS |

#### 品牌检测规则
- 判别逻辑：商品标题中含有**拉丁字母**（a-z/A-Z）即判定为有品牌
- 纯西里尔字母（俄语）、中文、数字的标题判定为无品牌

**不负责：**
- 不处理规则的动态配置（规则硬编码，参数来自 settings.json）
- 不做模糊匹配或AI辅助分类

---

### 2.4 `browser.py` — 浏览器自动化

**能力边界：**
- 支持两种模式：
  - **CDP模式**：连接用户手动启动的Chrome（`CHROME_CDP_URL`）
  - **独立模式**：Playwright自行启动Chrome
- 管理毛子ERP页面（`ozon.maozierp.com`）的生命周期
- 通过注入JS调用毛子API：
  - `fetch_top_list_page()`：榜单分页查询
  - `fetch_sku3_batch()`：批量SKU3详情查询
  - `fetch_seller_offers()`：跟卖列表查询
- 登录态管理：
  - `check_login_status()`：检测 localStorage Token 和插件"请登录"按钮
  - `ensure_authenticated()`：自动点击"请登录"按钮恢复登录
  - `get_maozi_token_from_page()`：提取Token
- 卖家主页抓取：`fetch_seller_home_products()` 打开卖家URL并滚动提取商品

**不负责：**
- 不管理浏览器生命周期（CDP模式下不关闭浏览器）
- 不做验证码自动识别
- 不支持多浏览器并发（单实例设计）

**CDP连接安全原则：**
1. 使用 `connect_over_cdp`，**不** `launch`
2. 断开时用 `p.stop()`，**不** `browser.close()`
3. Token不在日志中打印

---

### 2.5 `maozi_api.py` — 毛子ERP API客户端

**能力边界：**
- 直接调用毛子API（非浏览器注入方式）：
  - `sku3()`：单SKU查询
  - `sku3_batch()`：批量SKU查询（内部并发）
- 使用 requests Session 管理HTTP连接
- 请求头包含 `Authorization: Bearer {token}`, `Client: plugin`

**不负责：**
- 不处理Token续期（Token从 `settings.json` 或浏览器页面获取）
- 不做CORS相关的回退逻辑

---

### 2.6 `repository.py` — 数据仓库

**能力边界：**

#### ozon_categories
- `list_categories_by_level(level)`：按层级列出类目
- `upsert_category()`：插入或更新单条类目
- `bulk_upsert_categories_from_items(items)`：从榜单商品列表中提取类目ID，批量去重后写入 ozon_categories（采集过程中自动增长类目树）

#### seed_pool_skus
- `bulk_upsert_seed_pool()`：批量写入榜单种子（自动计算快照哈希去重）
- `list_pending_seeds()`：获取待处理的种子
- `mark_seed_status()`：标记种子处理状态

#### seller_shops
- `upsert_seller_shop()`：写入/更新卖家记录（含 `source_sku` 和 `source_table` 溯源）
- `list_due_sellers()`：列出到期可采集的卖家（按创建时间倒序，新卖家优先）
- `freeze_seller(key, months)`：冻结卖家指定月数
- `freeze_seller_permanent(key)`：永久冻结（品牌卖家）
- `mark_seller_collected(key, count)`：标记已采集+记录达标数

#### sku_products
- `parse_sku3_response()`：解析毛子SKU3 API返回数据为标准指标字典
- `upsert_sku_product()`：将合格SKU写入产品表（含来源追踪）
- `count_sku_products()` / `count_seed_pool()` / `count_seller_shops()`：统计计数

**不负责：**
- 不做复杂查询或报表
- 不处理事务嵌套（单条SQL即事务）

---

### 2.7 `ranking_collector.py` — 榜单采集

**能力边界：**

**执行流程：**
```
断点检查(如启用resume) → 跳过已完成类目
  └── 类目迭代(0/1/2/3级)
        └── 页面迭代(page_from → page_to, 每页50条)
              ├── 榜单API查询
              ├── 类目自增：提取cate1_id/cate2_id/cate3_id → 写入 ozon_categories
              ├── 种子预筛选（Seed Rule）
              ├── 合格 → 写入 seed_pool_skus
              ├── 批量SKU3获取（60条/批, 5并发）
              ├── 合格商品判定（Selection Rule）
              ├── 合格 → 写入 sku_products + 发现卖家写入 seller_shops
              └── 每页完成 → 保存断点到 collection_checkpoint
```

**类目层级说明：**
- `level=0`：不选类目，采集整个榜单
- `level=1`：遍历 `ozon_categories` 中所有1级类目（约23个）
- `level=2`：遍历所有2级类目
- `level=3`：遍历所有3级类目

**错误处理策略：**
- 单页失败 → 跳过，继续下一页
- Token失效 → 自动调用 `ensure_authenticated()` 恢复
- 恢复后仍失败 → 跳过

**不负责：**
- 不处理多类目并发（串行遍历）
- 不持久化采集进度（重跑会从头开始）
- 不获取跟卖信息

---

### 2.8 `seller_collector.py` — 卖家主页采集

**能力边界：**

**执行流程：**
```
查询到期卖家（新入库优先）
  └── 打开卖家主页（Ozon entrypoint API 翻页获取全部商品）
        ├── 品牌检测（前24条商品标题含拉丁字母）
        │     └── 全部有品牌 → 永久冻结
        ├── 价格预筛（20-1000 CNY）
        ├── 提取SKU列表
        ├── 分批批量获取SKU3详情（全部SKU，非仅前60个）
        ├── 两步筛选:
        │     ├── 步骤1: SKU3先筛(skip_offer_count=True, 仅SKU3字段)
        │     │     └── 不通过 → continue（省去跟卖API请求）
        │     └── 步骤2: 通过 → fetch_seller_offers获取跟卖人数 → 完整判定
        └── 冻结策略:
              ├── 有合格SKU → 冻结1个月
              └── 无合格SKU → 冻结6个月
```

**冻结策略汇总：**

| 场景 | 冻结时长 | 原因 |
|------|----------|------|
| 全部商品有品牌 | 永久 (2099年) | 品牌卖家，不值得再采 |
| 有合格SKU | 1个月 | 有价值，但不需要频繁更新 |
| 无合格SKU | 6个月 | 暂无价值，长期跳过 |
| 页面加载失败 | 不冻结 | 下次重试 |

**错误处理策略：**
- 启动时先验证登录态（`ensure_authenticated` 最多重试3次）
- 每处理一个卖家后检查认证，防止Token在采集过程中失效
- 个别SKU数据不可获取 → 跳过，继续下一个
- Token整体失效 → 自动调用 `ensure_authenticated()` 恢复

**不负责：**
- 不做卖家页面深层滚动
- 不递归发现跟卖卖家（仅处理已有的）
- 不并发处理多个卖家（串行遍历）

---

### 2.9 `gui.py` — GUI 界面

**能力边界：**
- 双模式选择：榜单采集 / 卖家主页采集
- 参数配置：
  - 榜单：类目层级、页数、过滤条件
  - 卖家：处理上限、超时
  - 循环：是否无人值守、等待时间
- **暂存按钮**：将GUI配置写入 `config.json`
- 启动/停止采集
- **启动浏览器**按钮：启动Chrome CDP
- 实时状态检测：CDP连接、DB连接、SKU/卖家计数
- 运行日志显示（暗色主题，错误高亮）

**不负责：**
- 不提供数据库管理功能（需用其他工具）
- 不提供数据导出/报表
- 不提供运行历史记录

---

### 2.10 `main.py` — 编排器

**能力边界：**
- 初始化阶段：确认数据库表结构（执行 `sql/init.sql`）
- 运行阶段：
  1. 先执行榜单采集
  2. 榜单完成后自动执行卖家采集
  3. 循环模式（forever=true）：完成后休眠指定秒数再继续

**不负责：**
- 不做任务调度（cron/定时）
- 不做失败重试策略编排（各模块内部自行处理）

---

### 2.11 `captcha_handler.py` — 验证码与登录恢复

**能力边界：**

| 功能 | 入口函数 | 说明 |
|------|----------|------|
| 滑块验证码 | `SliderHandler(page).solve()` | 拟人化拖动 vben-spine 滑块，支持重试 |
| 自动登录 | `auto_login(context)` | 打开登录页 → 填用户名密码 → 滑块验证 → 点击登录 → 等待成功 |
| 插件弹窗处理 | `handle_plugin_login_popup(context)` | 检测 Ozon 页面中毛子插件的"请登录"按钮 → 点击 → 自动登录 |
| 多级统一恢复 | `unified_login_recovery(context)` | 按优先级：1.插件弹窗 → 2.扫描登录页 → 3.主动登录 |
| 状态检测 | `detect_captcha_present(page)` / `detect_login_expired(page)` | 检测滑块验证码或登录页 |

**多级恢复执行顺序（`unified_login_recovery`）：**

```
方法1: 刷新 Ozon 页面 → 检测插件"请登录"弹窗 → 点击 → auto_login
  └── 失败 ↓
方法2: 扫描所有毛子页面 → 如果存在登录页 → auto_login
  └── 失败 ↓
方法3: 主动打开登录页 → auto_login
  └── 失败 → 飞书告警 + 返回 False
```

**拟人化滑块拖动（`SliderHandler._human_like_drag`）：**
- 缓动曲线：慢-快-慢（ease-in-out），40~60步
- Y轴高斯抖动（σ=1.5），模拟手抖
- 末尾随机过冲（0~3%），模拟惯性
- 不均匀步长时间（3~15ms）

**自动登录流程（`auto_login`）：**
1. 导航到 `ozon.maozierp.com/#/auth/login`
2. 轮询等待"请按住滑块拖动"文字出现（15s超时）
3. 填写用户名/密码（前两个 `input` 元素）
4. 勾选"记住账号"复选框
5. 调用 `SliderHandler.solve()` 拖动滑块
6. 点击"登录"按钮
7. 轮询 `localStorage` 中 `maozierp-core-access.accessToken` 出现（15s超时）

**不负责：**
- 不处理 Ozon 自身的验证码（Cloudflare Turnstile 等）
- 不处理账号封禁/密码过期等非技术性登录失败
- 飞书告警依赖 `feishu.py` 模块存在

---

## 三、数据库表结构

### 3.1 `ozon_categories` — 类目树
| 列 | 类型 | 说明 |
|----|------|------|
| category_id | BIGINT | Ozon类目ID |
| name_zh | VARCHAR(255) | 中文名称 |
| name_en | VARCHAR(255) | 英文名称 |
| parent_id | BIGINT | 父级category_id |
| level | TINYINT | 层级(1/2/3) |

### 3.2 `seed_pool_skus` — 榜单种子池
存储从榜单中筛选出的合格种子商品，带有榜单来源信息（页数、排名、快照哈希等）。

### 3.3 `seller_shops` — 卖家店铺表
| 列 | 类型 | 说明 |
|----|------|------|
| seller_key | CHAR(40) | 卖家URL的SHA1 |
| source_sku | VARCHAR(32) | **新增** 来源SKU |
| source_table | VARCHAR(64) | **新增** 来源表(seed_pool_skus/sku_products) |
| next_collect_after | DATETIME | 下次可采集时间(冻结控制) |
| qualified_sku_count | INT | **新增** 最近采集达标数 |

### 3.4 `sku_products` — 合格SKU表
存储最终通过筛选的合格SKU，包含完整的毛子SKU3指标数据和商品信息。
**新增列**：`source_sku`, `source_table`（来源追踪）

---

## 四、数据流全景

```
┌──────────────┐
│  榜单API     │  毛子ERP排行榜
└──────┬───────┘
       │ 每页50条
       ▼
┌──────────────┐
│ 种子预筛选   │  Seed Rule (月销3-200, FBS, 重量<5kg等)
└──────┬───────┘
       │ 通过
       ▼
┌──────────────┐
│ seed_pool_   │  榜单种子池
│ skus         │  记录: SKU, 榜单数据, 快照哈希
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ SKU3 API     │  毛子ERP详细指标查询
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 合格商品判定 │  Selection Rule (无品牌, 月销1-65, 20-1000元CNY等)
└──────┬───────┘
       │ 通过
       ▼
┌──────────────┐
│ sku_         │  合格SKU产品表
│ products     │  完整指标数据
└──────────────┘

同时，合格商品的跟卖卖家：
       │
       ▼
┌──────────────┐
│ seller_      │  卖家店铺表
│ shops        │  source_sku + source_table 溯源
└──────┬───────┘
       │ 到期采集
       ▼
┌──────────────┐
│ 卖家主页采集 │  品牌检测 → 冻结策略
└──────────────┘
```

---

## 五、部署与运行

### 5.1 环境准备
```bash
pip install -r requirements.txt
# 安装 Playwright 浏览器
playwright install chromium
```

### 5.2 配置
```bash
cp settings.example.json settings.json
# 编辑 settings.json，填入数据库密码、MAOZI_TOKEN 等
```

### 5.3 初始化数据库
```bash
python -m task.seed_categories
```

### 5.4 启动GUI
```bash
run_gui.bat
# 或从上级目录运行: python -m maozi_collect_mini.gui
```

### 5.5 命令行直接运行
```bash
cd c:\project\maozi-plugin-2.3.2
python -m maozi_collect_mini.main
```


---

## 六、与原项目 ozon_selection_pipeline 的差异

| 维度 | 原项目 | Mini版 |
|------|--------|--------|
| 采集模式 | 4种模式(卖家循环、榜单网络、种子池、多类目) | 2种(榜单采集、卖家主页) |
| 数据库表 | 15+ 张表 | 4张核心表 |
| 代码量 | ~8000行 | ~2500行 |
| GUI | 完整仪表盘+新手引导+多Tab | 精简配置面板 |
| 并发 | 多Worker线程池+CDP管道 | 串行处理 |
| 任务队列 | crawl_tasks表+优先级 | 无，直接遍历 |
| 飞书通知 | 支持 | 不支持 |
| 数据库同步 | 多Profile自动同步 | 无 |

---

## 七、已知限制

1. **单线程采集**：榜单和卖家采集均串行执行，不支持并行
2. **无断点续采**：中断后重新运行会从头开始
3. **无进度持久化**：不保存采集进度到数据库
4. **限Windows**：GUI基于Tkinter，命令行无此限制
5. **无代理支持**：简化的BrowserClient不支持代理配置
6. **不处理Cloudflare**：遇到CF验证需手动介入
7. **卖家页简单抓取**：基于DOM选择器，可能因页面改版失效
