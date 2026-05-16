# source_matching：Ozon 主图匹配 1688 货源

`source_matching` 是当前项目里的货源匹配子项目。它的目标很单一：拿 Ozon 商品主图去 1688 图搜，抓取相似货源卡片，并把匹配结果沉淀到 MySQL，方便后续做选品、比价和供应链筛选。

这个目录目前不是一个完整的后端服务，而是一个可重复运行的批处理脚本。

---

## 一句话流程

```text
Ozon SKU 主图 -> 本地 downloads 图片池 -> Playwright 打开 1688 图搜 -> 上传图片 -> 一件代发筛选 -> 解析结果卡片 -> 写入 MySQL
```

---

## 当前项目定位

当前大项目里有几个容易混淆的目录：

- `ozon_selection_pipeline/`
  Ozon 选品采集主流程，负责从毛子 ERP、Ozon 前端、卖家主页等来源扩展 SKU，并写入 `sku_products` 等表。
- `ozon_pic/`
  已下载或手工准备的 Ozon 商品主图目录。
- `source_matching/`
  本目录，只负责把 Ozon 主图拿去 1688 图搜并保存货源候选。
- `maozi-plugin-2.3.2/`、`乌拉-ozon助手/`
  浏览器插件成品目录，不是 `source_matching` 的核心代码。
- `scripts/`、`test_*.py`、`tmp_desktop_code/`
  历史脚本、诊断脚本或临时代码，后续重构时应优先清理或归档。

---

## 目录结构

```text
source_matching/
├── README.md
├── .env.example
├── requirements.txt
├── run_match_source_products.py
├── match_source_products.py
└── sql/
    └── 001_init.sql
```

运行后还会生成这些目录：

```text
source_matching/
├── downloads/   # 同步或下载后的待搜索图片
├── results/     # 预留结果目录，当前主结果写入数据库
└── profiles/    # Playwright 持久化 Chrome 用户目录，默认在这里
```

---

## 核心文件说明

### `run_match_source_products.py`

轻量入口文件，只调用 `match_source_products.main()`。

日常运行建议用这个入口：

```powershell
python source_matching\run_match_source_products.py
```

### `match_source_products.py`

核心脚本，当前所有主要逻辑都集中在这里：

- 读取 `.env` 配置。
- 初始化 MySQL 表。
- 从数据库 `sku_products.main_image_url` 下载图片。
- 从本地 `INPUT_IMAGE_DIR` 复制图片。
- 用 Playwright 启动持久化 Chrome。
- 打开 1688 图搜页并上传图片。
- 拼接带 `offerTags` 和排序参数的搜索 URL。
- 解析搜索结果卡片。
- 将结果写入 `matched_source_products`。
- 将每次运行写入 `matched_source_runs`。

### `sql/001_init.sql`

创建两张结果表：

- `matched_source_products`
  每个 SKU、每张图、每个匹配排名对应一行，用于保存 1688 货源候选。
- `matched_source_runs`
  每次运行的审计表，记录输入目录、下载目录、Chrome 配置、成功数量、失败数量和错误信息。

### `.env.example`

配置模板。使用前复制为 `.env`，再按本机环境修改数据库、Chrome、输入目录和图搜参数。

### `requirements.txt`

Python 依赖：

- `pymysql`：连接 MySQL。
- `playwright`：自动打开 Chrome 和操作 1688 图搜。
- `python-dotenv`：读取 `.env`。
- `requests`：下载远程主图。
- `Pillow`：读取图片尺寸、格式和 MIME 信息。

---

## 数据来源

脚本会合并两类图片来源。

### 1. 数据库图片

从当前配置的数据库读取：

```sql
SELECT sku, main_image_url
FROM sku_products
WHERE main_image_url IS NOT NULL AND main_image_url != ''
ORDER BY sku
```

读取后会把远程图片下载到 `DOWNLOAD_DIR`，文件名为：

```text
{sku}.{jpg/png/webp/...}
```

如果只想限制从数据库下载的数量，可以设置：

```env
SOURCE_IMAGE_TABLE_LIMIT=100
```

如果不设置或设为 `0`，表示不加限制。

### 2. 本地图片目录

默认读取大项目根目录下的：

```text
C:\project\maozi-plugin-2.3.2\ozon_pic
```

脚本会把图片复制到 `DOWNLOAD_DIR`。如果目录里存在文件名以 `sku` 开头的图片，会优先只处理这些图片；否则处理目录下全部非隐藏文件。

---

## 输出结果

### 1. 图片缓存

默认输出到：

```text
C:\project\maozi-plugin-2.3.2\source_matching\downloads
```

这里是实际上传到 1688 图搜的图片池。

### 2. MySQL 结果表

核心结果写入 `matched_source_products`。

重要字段包括：

- `sku`：来自图片文件名或数据库 SKU。
- `source_image_*`：源图片路径、文件名、哈希、大小、宽高、MIME。
- `search_url`：实际打开的 1688 图搜 URL。
- `search_image_id`、`search_image_id_list`：1688 上传图片后返回或页面 URL 中解析到的图片 ID。
- `search_offer_tags`：图搜筛选标签，默认 `1988226`。
- `search_sort_type`：排序方式，默认 `normal`。
- `matched_rank`：匹配排名，从 1 开始。
- `matched_score`：页面 HTML 中解析到的匹配分数，可能为空。
- `matched_title`：1688 卡片标题。
- `matched_offer_id`：1688 offer ID。
- `matched_product_url`：1688 商品链接。
- `matched_main_image_url`：1688 卡片主图。
- `matched_shop_name`、`matched_shop_uid`、`matched_shop_years_text`：店铺信息。
- `matched_price_text`、`matched_price_amount`、`matched_currency`：价格信息。
- `matched_sales_text`、`matched_min_order_text`：销量和起批信息。
- `matched_services_json`、`matched_tags_json`、`matched_attributes_json`：服务、标签、属性。
- `matched_card_raw_json`：卡片文本和 HTML 原始快照。
- `match_status`：`matched`、`no_result` 或 `error`。
- `match_reason`：没有结果或异常时的原因。

每次运行还会写入 `matched_source_runs`，用于追踪本轮运行是否成功、处理了多少图片、失败了多少图片。

---

## 安装与初始化

建议在项目根目录执行。

### 1. 安装 Python 依赖

```powershell
python -m pip install -r source_matching\requirements.txt
```

### 2. 安装 Playwright 浏览器依赖

如果本机还没有安装 Playwright 浏览器：

```powershell
python -m playwright install chromium
```

### 3. 复制配置文件

```powershell
copy source_matching\.env.example source_matching\.env
```

### 4. 修改 `.env`

重点检查这些配置：

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DB_NAME=source_matching

CHROME_PROFILE_DIR=source_matching/profiles/profile-001
CHROME_EXECUTABLE_PATH=
CHROME_CHANNEL=chrome
CHROME_PROXY_SERVER=
CHROME_HEADLESS=false

INPUT_IMAGE_DIR=C:\project\maozi-plugin-2.3.2\ozon_pic
DOWNLOAD_DIR=C:\project\maozi-plugin-2.3.2\source_matching\downloads
RESULT_DIR=C:\project\maozi-plugin-2.3.2\source_matching\results
TARGET_OFFER_TAGS=1988226
TARGET_SORT_TYPE=normal
SEARCH_PAGE_BASE=https://s.1688.com/youyuan/index.htm
SEARCH_WAIT_TIMEOUT_MS=60000
MAX_MATCHES_PER_IMAGE=10
SOURCE_IMAGE_TABLE_LIMIT=0
```

注意：当前 `sql/001_init.sql` 实际创建并使用的是 `ozon_selection` 数据库，而 `.env.example` 默认写的是 `source_matching`。如果不调整 SQL，建议先把 `.env` 里的 `DB_NAME` 改为：

```env
DB_NAME=ozon_selection
```

后续重构时应该统一这个库名，避免初始化和运行时连接到不同数据库。

### 5. 初始化数据库

脚本启动时会自动执行：

```text
source_matching/sql/001_init.sql
```

如果希望手工初始化，也可以在 MySQL 客户端中执行该 SQL 文件。

---

## 运行方式

在项目根目录执行：

```powershell
python source_matching\run_match_source_products.py
```

运行过程大致如下：

1. 自动执行 SQL 初始化。
2. 创建 `downloads` 和 `results` 目录。
3. 创建一条 `matched_source_runs` 运行记录，状态为 `running`。
4. 从数据库下载 `sku_products.main_image_url`。
5. 从 `INPUT_IMAGE_DIR` 复制本地图片。
6. 合并图片列表，按文件名排序。
7. 启动持久化 Chrome。
8. 逐张图片打开 1688 图搜、上传、解析卡片。
9. 每张图片的每个候选结果写入 `matched_source_products`。
10. 运行结束后更新 `matched_source_runs` 为 `success` 或 `failed`。

---

## Chrome 与 1688 登录态

脚本使用 Playwright 的持久化浏览器上下文：

```text
CHROME_PROFILE_DIR=source_matching/profiles/profile-001
```

这意味着：

- 第一次运行可能需要你在弹出的 Chrome 里登录 1688。
- 登录状态会保存在 `CHROME_PROFILE_DIR`。
- 后续运行复用同一个 profile，减少重复登录。
- 如果 1688 页面要求验证，需要先在浏览器里手工完成。

当前 `CHROME_CDP_URL` 和 `CHROME_REMOTE_DEBUGGING_PORT` 已在配置类中保留，但核心运行逻辑实际使用的是 `launch_persistent_context`，不是附着到已有 CDP 浏览器。后续如果要复用已打开的真实浏览器，需要补齐这部分逻辑。

---

## 关键配置说明

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `DB_HOST` | `localhost` | MySQL 地址 |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | MySQL 用户 |
| `DB_PASSWORD` | `root` | MySQL 密码 |
| `DB_NAME` | `source_matching` | 运行时连接的数据库，当前建议改为 `ozon_selection` |
| `CHROME_PROFILE_DIR` | `source_matching/profiles/profile-001` | Playwright 持久化 Chrome 用户目录 |
| `CHROME_EXECUTABLE_PATH` | 空 | 指定 Chrome 可执行文件路径，空时用 `CHROME_CHANNEL` |
| `CHROME_CHANNEL` | `chrome` | Playwright 启动的浏览器渠道 |
| `CHROME_PROXY_SERVER` | 空 | 代理服务器，例如 `http://127.0.0.1:7890` |
| `CHROME_HEADLESS` | `false` | 是否无头运行，涉及登录和验证时建议保持 `false` |
| `INPUT_IMAGE_DIR` | `../ozon_pic` | 本地主图输入目录 |
| `DOWNLOAD_DIR` | `source_matching/downloads` | 图片下载与复制后的统一目录 |
| `RESULT_DIR` | `source_matching/results` | 预留结果目录 |
| `TARGET_OFFER_TAGS` | `1988226` | 1688 图搜筛选标签，当前用于一件代发筛选 |
| `TARGET_SORT_TYPE` | `normal` | 1688 图搜排序方式 |
| `SEARCH_PAGE_BASE` | `https://s.1688.com/youyuan/index.htm` | 1688 图搜入口 |
| `SEARCH_WAIT_TIMEOUT_MS` | `60000` | 页面和接口等待超时时间 |
| `MAX_MATCHES_PER_IMAGE` | `10` | 每张图最多保存多少个候选卡片 |
| `SOURCE_IMAGE_TABLE_LIMIT` | `0` | 从数据库读取主图的数量限制，`0` 表示不限 |

---

## 代码执行链路

下面是从入口到落库的真实调用链：

```text
run_match_source_products.py
└── main()
    ├── ensure_sql()
    │   └── run_sql_file(sql/001_init.sql)
    ├── upsert_run()
    ├── sync_images()
    │   ├── download_remote_source_images()
    │   │   └── fetch_source_sku_images()
    │   ├── ensure_image_downloads()
    │   └── find_images()
    ├── sync_playwright()
    ├── chromium.launch_persistent_context()
    ├── match_one()
    │   ├── image_meta()
    │   ├── upload_image_and_open_search()
    │   │   ├── 上传图片
    │   │   ├── 解析 imageId / imageIdList
    │   │   └── build_search_url()
    │   ├── extract_offer_cards()
    │   └── save_match_row()
    └── finish_run()
```

---

## 解析逻辑说明

`extract_offer_cards()` 当前通过页面选择器读取 1688 卡片：

```text
[data-splus-logkey*="offerlist.offer"], .major-offer
```

它会尽量解析：

- 标题
- 商品链接
- offer ID
- 主图
- 店铺名
- 店铺 UID
- 店铺年限
- 价格
- 销量
- 起批量
- 服务标签
- 属性文本
- 原始文本与 HTML

因为 1688 前端 class 名可能经常变化，所以这部分是后续最容易失效的地方。重构时建议把“页面解析器”从主脚本中拆出来，并保留原始 HTML 快照用于回放测试。

---

## 常见问题

### 1. 没有找到图片

终端输出：

```text
no source images found
```

检查：

- `INPUT_IMAGE_DIR` 是否存在。
- `INPUT_IMAGE_DIR` 下是否有图片。
- `DOWNLOAD_DIR` 是否有图片。
- 数据库 `sku_products.main_image_url` 是否有值。
- `.env` 的 `DB_NAME` 是否连接到了包含 `sku_products` 的数据库。

### 2. 数据库初始化后运行仍然找不到表

重点检查 `.env` 的 `DB_NAME` 和 `sql/001_init.sql` 的数据库名是否一致。

当前 SQL 创建的是：

```sql
CREATE DATABASE IF NOT EXISTS `ozon_selection`;
USE `ozon_selection`;
```

但 `.env.example` 默认是：

```env
DB_NAME=source_matching
```

因此当前建议先使用：

```env
DB_NAME=ozon_selection
```

### 3. 1688 要求登录或验证

保持 `CHROME_HEADLESS=false`，让浏览器窗口可见，然后在弹出的 Chrome 中手工完成登录或验证。完成后再次运行，登录态会保存在 `CHROME_PROFILE_DIR`。

### 4. 上传图片后拿不到 `imageId`

可能原因：

- 1688 接口结构变化。
- 上传请求被风控拦截。
- 页面没有成功跳转到图搜结果页。
- 网络或代理异常。

脚本会先监听上传接口响应，再从页面 URL 里解析 `imageId`；两者都失败时会记录为 `error`。

### 5. 结果卡片字段为空

1688 页面结构变化时，部分 CSS 选择器可能失效。优先检查 `matched_card_raw_json` 中保存的 `text` 和 `html`，再调整 `extract_offer_cards()` 的解析逻辑。

---

## 当前已知问题

- 所有逻辑集中在一个 700 多行脚本里，职责边界不清晰。
- `.env.example` 的 `DB_NAME=source_matching` 与 SQL 里的 `ozon_selection` 不一致。
- `CHROME_CDP_URL` 配置存在，但当前主流程没有实际使用。
- `RESULT_DIR` 会创建，但当前没有稳定写文件结果，主结果都在 MySQL。
- 1688 页面解析依赖前端 class 和 DOM 结构，抗变化能力有限。
- 没有命令行参数，所有运行参数都依赖 `.env`。
- 没有自动化测试，尤其缺少 HTML 快照解析测试。
- 失败重试策略较弱，单图失败只记录错误，不做分级重试。

---

## 建议的重构方向

如果后续要简化这个项目，建议按下面顺序处理。

### 1. 先统一配置和数据库

- 明确结果表到底放在 `ozon_selection` 还是独立的 `source_matching` 数据库。
- 同步修改 `.env.example`、`sql/001_init.sql` 和 README。
- 把没有实际使用的配置项删掉或补齐实现。

### 2. 拆分 `match_source_products.py`

建议拆成：

```text
config.py        # Settings 和 .env 加载
db.py            # connect_db / execute / fetch_all / migrations
images.py        # 本地复制、远程下载、图片元信息
browser.py       # Playwright 启动、上传图片、打开图搜页
parser.py        # extract_offer_cards 和字段解析
repository.py    # matched_source_products / matched_source_runs 落库
cli.py           # main 和命令行参数
```

### 3. 增加命令行参数

优先支持：

```text
--limit
--input-dir
--download-dir
--headless
--max-matches
--skip-db-images
--skip-local-images
```

这样可以减少频繁修改 `.env`。

### 4. 给解析器加快照测试

把 `matched_card_raw_json.html` 保存为测试 fixture，然后让 `parser.py` 在离线环境下验证字段解析，避免每次都依赖真实 1688 页面。

### 5. 明确与主选品项目的边界

`source_matching` 应只消费主项目产出的 SKU 和主图，不应该混入 Ozon 采集逻辑。后续可以把它定义为：

```text
输入：sku_products.main_image_url 或图片目录
处理：1688 图搜匹配
输出：matched_source_products / matched_source_runs
```

---

## 快速排查 SQL

查看最近运行记录：

```sql
SELECT *
FROM matched_source_runs
ORDER BY id DESC
LIMIT 10;
```

查看每个 SKU 的前 3 个匹配结果：

```sql
SELECT sku, matched_rank, matched_title, matched_price_amount, matched_shop_name, matched_product_url, match_status
FROM matched_source_products
WHERE matched_rank <= 3
ORDER BY sku, matched_rank;
```

查看失败图片：

```sql
SELECT sku, source_image_file, match_status, match_reason, updated_at
FROM matched_source_products
WHERE match_status = 'error'
ORDER BY updated_at DESC;
```

查看没有结果的图片：

```sql
SELECT sku, source_image_file, match_reason, updated_at
FROM matched_source_products
WHERE match_status = 'no_result'
ORDER BY updated_at DESC;
```

---

## 当前结论

`source_matching` 当前可以视为一个“能跑但需要拆分”的批处理工具。它已经具备完整的数据闭环：图片输入、1688 图搜、结果解析、MySQL 落库和运行审计。下一步简化项目时，优先处理配置/数据库命名不一致和单文件过大的问题，收益最大。
