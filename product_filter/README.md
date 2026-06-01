# product_filter — OZON 产品临界采购价格测算

基于 GUOO realFBS 物流费率表，对 `sku_products` 中的 FBS 无品牌产品，逐个估算**不亏本的临界采购价格（人民币）**。

---

## 目录

- [核心公式](#核心公式)
- [模块结构](#模块结构)
- [快速开始](#快速开始)
  - [独立使用（纯计算）](#独立使用纯计算)
  - [命令行批量](#命令行批量)
  - [数据库模式](#数据库模式)
- [配置参数](#配置参数)
- [汇率说明](#汇率说明)
- [物流渠道匹配](#物流渠道匹配)
- [API 参考](#api-参考)
- [关于 original_price 字段](#关于-original_price-字段)
- [依赖](#依赖)

---

## 核心公式

```
售价 = 利润 + 运费×1.2 + 佣金 + 贴单费 + 损耗 + 采购成本
```

展开：

| 项目 | 公式 | 说明 |
|------|------|------|
| 利润 | 售价 × 0.15 | 15% 目标利润率 |
| 佣金 | 售价 × 0.15 | OZON 平台佣金约 15% |
| 损耗 | 售价 × 0.03 | 退货/破损等损耗 3% |
| 贴单费 | 0.5 元/件 | 固定贴单成本 |
| 运费 | 基础运费 × 1.2 | 安全系数 1.2，覆盖运费波动 |
| 采购成本 | **售价×0.67 − 运费×1.2 − 0.5** | 临界值，高于此值则亏本 |

> **推导**：采购成本 = 售价 − 利润 − 佣金 − 损耗 − 运费×1.2 − 0.5  
> 　　　　　　　 = 售价 − 售价×0.15 − 售价×0.15 − 售价×0.03 − 运费×1.2 − 0.5  
> 　　　　　　　 = 售价×(1−0.15−0.15−0.03) − 运费×1.2 − 0.5  
> 　　　　　　　 = **售价×0.67 − 运费×1.2 − 0.5**

### 示例

| 售价(₽) | 重量(kg) | 售价(¥) | 安全运费(¥) | 临界采购价(¥) | 可行? |
|----------|----------|---------|------------|---------------|-------|
| 1,000 | 0.2 | 86.64 | 9.84 | **47.71** | Y |
| 5,000 | 0.8 | 433.20 | 44.93 | **244.82** | Y |
| 300 | 0.05 | 25.99 | 5.16 | **11.75** | Y |

> 以上按保守汇率 0.08664（₽→¥，正常汇率 0.0912 下浮 5%）计算。

---

## 模块结构

```
product_filter/
├── __init__.py          # 包入口，导出公共 API
├── config.py            # 可调参数（利润率、佣金率、汇率、物流表路径等）
├── logistics.py         # 从 Excel 解析 GUOO realFBS 物流费率，渠道匹配
├── calculator.py        # 临界采购价格计算核心
├── db_reader.py         # 数据库读取（流式分批、防掉线连接池）
├── main.py              # 命令行入口
└── README.md            # 本文件
```

---

## 快速开始

### 独立使用（纯计算）

无需数据库，直接传入价格和重量：

```python
from decimal import Decimal
from product_filter import calc_critical_procurement_price

result = calc_critical_procurement_price(
    price_rub=Decimal("1000"),   # 售价(卢布)
    weight_kg=Decimal("0.2"),    # 重量(kg)
    sku="123456789",
    title="示例产品",
)

print(f"临界采购价格: {result.critical_procurement_cny} 元")
print(f"匹配渠道: {result.matched_channel.delivery_name}")
print(f"安全运费: {result.shipping_safe_cny} 元")
print(f"可行: {result.is_viable}")

# 查看成本拆解
print(f"利润: {result.profit_cny}  佣金: {result.commission_cny}")
print(f"损耗: {result.loss_cny}  贴单费: 0.5")
```

### 命令行批量

请确保已配置数据库连接环境变量。

```bash
# 分页模式（默认前 100 条）
python -m product_filter.main

# 全量流式计算（分批读取，避免内存溢出）
python -m product_filter.main --all

# 计算单个产品
python -m product_filter.main --sku 123456789

# 自定义分页
python -m product_filter.main --limit 500 --offset 200
```

输出格式：

```
SKU            标题                               售价(¥)   重量kg     运费(¥)   安全运费(¥)    临界采购价(¥)   毛利% 渠道                       可行
----------------------------------------------------------------------------------------------------------------------------------------------------
123456789      示例产品标题                          80.00   0.200     8.20      9.84         43.26   45.9 GUOO Economy Extra Small    Y
```

### 数据库模式

在代码中调用，获得结构化结果：

```python
from product_filter import fetch_products_stream, product_critical_price, load_logistics_table

channels = load_logistics_table()

for batch in fetch_products_stream(batch_size=500):
    for prod in batch:
        r = product_critical_price(prod, channels=channels)
        if r.is_viable:
            print(f"{r.sku} 临界采购价={r.critical_procurement_cny} 元")
```

---

## 配置参数

所有参数集中在 `config.py` 的 `ProductFilterSettings` 中，通过环境变量或直接修改代码调整：

| 参数 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| `profit_margin` | `0.15` | — | 目标利润率（15%） |
| `commission_rate` | `0.15` | — | OZON 佣金率（15%） |
| `loss_rate` | `0.03` | — | 损耗率（3%） |
| `sticker_fee_cny` | `0.5` | — | 贴单费（元/件） |
| `shipping_safety_factor` | `1.2` | — | 运费安全系数 |
| `rub_to_cny_rate` | `0.08664` | `RUB_TO_CNY_RATE_SAFE` | 保守汇率（正常0.0912下浮5%） |
| `logistics_excel_path` | `../GUOO产品资费测算表…xlsx` | — | 物流费率 Excel 路径 |
| `logistics_sheet_name` | `GUOO realFBS资费试算表` | — | 工作表名称 |
| `db_host` | `127.0.0.1` | `MYSQL_HOST` | 数据库主机 |
| `db_port` | `3306` | `MYSQL_PORT` | 数据库端口 |
| `db_user` | `root` | `MYSQL_USER` | 数据库用户 |
| `db_password` | — | `MYSQL_PASSWORD` | 数据库密码 |
| `db_name` | `ozon_selection` | `MYSQL_DATABASE` | 数据库名 |

---

## 汇率说明

| 汇率 | 值 | 用途 |
|------|----|------|
| 正常汇率 | `0.0912` | 主项目 `ozon_pipeline` 日常价格比对用（`RUB_TO_CNY_RATE`） |
| **保守汇率** | **`0.08664`** | 本模块临界价格计算用（`RUB_TO_CNY_RATE_SAFE`） |

保守汇率 = 正常汇率 × 0.95，即正常汇率下浮 5%。效果：
- 同样 1000₽ 售价，正常 = 91.2 元，保守 = 86.64 元
- 人民币收入估算低约 5% → 临界采购价更保守 → **更不容易亏本**

可通过环境变量覆盖：
```bash
export RUB_TO_CNY_RATE_SAFE=0.09
```

---

## 物流渠道匹配

### 数据来源

从 `GUOO产品资费测算表【2026.4.15更新】(4).xlsx` 的 `GUOO realFBS资费试算表` 工作表中提取 **25 个物流渠道**。

### 费率结构

每个渠道含两个核心参数：

| 参数 | 含义 | 示例 |
|------|------|------|
| 每千克运费 | 按重量计费（元/kg） | 26 元/kg |
| 每票运费 | 固定票费（元/票） | 3 元/票 |

**运费 = 重量(kg) × 每千克运费 + 每票运费**

### 产品类型分类

| 类型 | 重量范围 | 货值范围(₽) | 典型场景 |
|------|----------|-------------|----------|
| Extra Small | 0.001–0.5 kg | 1–1,500 | 小饰品、3C 配件等超轻小件 |
| Budget | 0.501–30 kg | 不限 | 低客单价中等重量商品 |
| Small | 0.001–2 kg | 1,501–7,000 | 高客单价轻小件 |
| Big | 2.001–30 kg | 不限 | 大件商品 |
| Premium Small | 0.001–5 kg | 7,001–250,000 | 高价值轻小件 |
| Premium Big | 5.001–30 kg | 不限 | 高价值大件 |
| 邮政 E 邮宝 | 0.001–5 kg | 1–100,000 | 邮政渠道（含各国专线） |

### 运输方式优先级

| 优先级 | 运输方式 | 特点 |
|--------|----------|------|
| 1（最高） | 陆运 | 最经济，时效较慢 |
| 2 | 陆空联运 | 居中 |
| 3（最低） | 空运 | 最快，价格最高 |

### 匹配策略

1. **匹配精度打分**：同时满足重量+货值约束得 2 分，仅满足一项得 1 分
2. **同精度下**：优先经济渠道（陆运 > 陆空联运 > 空运）
3. **父行继承**：同产品类型下的子渠道自动继承父行的重量/货值限制

> 注意：部分产品类型（Big、Budget、Premium Big）的货值区间在原始 Excel 中未明确标注，当前版本不做推断。未来可结合 OZON 官方渠道规则完善。

---

## API 参考

### `calc_critical_procurement_price()`

```python
def calc_critical_procurement_price(
    price_rub: Optional[Decimal],   # 售价（默认按卢布处理）
    weight_kg: Decimal,             # 重量(kg)
    currency: Optional[str] = None, # 币种，默认 "RUB"
    channels: Optional[list[LogisticsChannel]] = None,  # 预加载渠道，不传则自动加载
    sku: str = "",
    title: str = "",
) -> CriticalPriceResult:
```

**返回** `CriticalPriceResult`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `sku` | `str` | 产品 SKU |
| `title` | `str` | 产品标题 |
| `price_raw` | `Decimal?` | 原始售价 |
| `currency` | `str?` | 原始币种 |
| `price_cny` | `Decimal?` | 转换后人民币售价 |
| `weight_kg` | `Decimal` | 重量(kg) |
| `matched_channel` | `LogisticsChannel?` | 匹配的物流渠道 |
| `shipping_cost_cny` | `Decimal` | 基础运费(元) |
| `shipping_safe_cny` | `Decimal` | 安全运费 = 基础 × 1.2 |
| `profit_cny` | `Decimal` | 利润(元) |
| `commission_cny` | `Decimal` | 佣金(元) |
| `loss_cny` | `Decimal` | 损耗(元) |
| `critical_procurement_cny` | `Decimal?` | **临界采购价格(元)** |
| `gross_margin_pct` | `Decimal?` | 毛利率(%) |
| `is_viable` | `bool` | 是否可行（临界价 > 0） |
| `error` | `str` | 错误信息 |

### `product_critical_price()`

直接从数据库查询结果字典计算，无需手动提取字段：

```python
def product_critical_price(
    product: dict,                                       # 数据库行字典
    channels: Optional[list[LogisticsChannel]] = None,
) -> CriticalPriceResult:
```

产品字典需含字段：`sku`, `title`, `price`/`card_price`, `currency`, `custom_weight_g`。

> 价格优先级：`card_price`（卡片实际售价） > `price`（标价）

### `estimate_shipping_cost()`

纯运费估算，不含安全系数：

```python
def estimate_shipping_cost(
    weight_kg: Decimal,
    price_rub: Optional[Decimal] = None,
    channels: Optional[list[LogisticsChannel]] = None,
    prefer_economy: bool = True,
) -> Optional[tuple[LogisticsChannel, Decimal]]:
```

### `load_logistics_table()`

从 Excel 加载全部物流渠道：

```python
def load_logistics_table(
    excel_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> list[LogisticsChannel]:
```

### `fetch_products()` / `fetch_products_stream()`

数据库读取函数，筛选条件：FBS 模式 + 无品牌 + 有重量 + 有价格。

```python
def fetch_products(limit=None, offset=0) -> list[dict]
def fetch_products_stream(batch_size=1000) -> Generator[list[dict]]
def fetch_product_by_sku(sku: str) -> Optional[dict]
```

---

## 关于 `original_price` 字段

`sku_products` 表中 **存在** `original_price` 字段（`DECIMAL(18,4) NULL`），定义于 `sql/001_init.sql:31`。

**但该字段目前是"僵尸字段"**——在所有 Python 业务代码（`repository.py`、`rules.py`、`browser_ozon.py` 等）中从未被写入或读取。实际业务流程中：

| 字段 | 含义 | 使用情况 |
|------|------|----------|
| `price` | 基础标价 | 被广泛读写，抓取时填入 |
| `card_price` | 卡片售价（折扣后实际价格） | 部分场景使用 |
| `original_price` | **原价（未使用）** | 有定义无写入，僵尸状态 |

当前模块的价格取值优先级：`card_price` → `price`。如需使用"原价"概念，建议：
1. 在抓取流程中（`browser_ozon.py` / `repository.py`）补上对 `original_price` 的写入逻辑
2. 将 OZON 页面上的划线原价写入该字段

---

## 依赖

- Python ≥ 3.9
- `openpyxl` — 读取 Excel 物流费率表
- `pymysql` — 数据库连接（仅在数据库模式下需要）

安装：
```bash
pip install openpyxl pymysql
```
