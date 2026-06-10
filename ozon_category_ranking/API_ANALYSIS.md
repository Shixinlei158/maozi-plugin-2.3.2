# Ozon 类目榜单 API 分析报告

> 分析日期：2026-06-10
> 分析方式：CDP 连接 127.0.0.1:9222，从已登录 Ozon 的浏览器中注入 JS 调用 API

---

## 一、核心 API 清单

| API | 方法 | 用途 |
|-----|------|------|
| `/api/entrypoint-api.bx/page/json/v2?url={path}` | GET | 获取任意页面的 widget 数据（类目页、卖家页、弹窗等） |
| `/api/composer-api.bx/_action/v2/getCatalogFilterValues` | POST | 获取指定类目的完整子类目树 |

### 1.1 entrypoint-api

```
GET https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={encodeURIComponent(path)}
```

**请求头：**
```
credentials: include  (必须，携带 Ozon 登录 Cookie)
```

**path 格式：**
- 类目页：`/category/{slug}-{category_id}/`
- 带翻页：`/category/{slug}-{category_id}/?page=2&paginator_token=...`
- 带排序：`/category/{slug}-{category_id}/?sorting=score`

**响应结构：**
```json
{
  "widgetStates": {
    "tileGridDesktop-{id}-default-1": { "items": [...] },     // 商品列表
    "infiniteVirtualPaginator-{id}-default-1": { "nextPage": "...", "prevPage": "..." },  // 翻页
    "filtersDesktop-{id}-default-1": { "sections": [...] },    // 筛选器（含类目树）
    "catalogMenu-{id}-default-1": { "categories": [...] },     // 导航菜单（一级类目列表）
    "breadCrumbs-{id}-default-1": { ... },                     // 面包屑
    "searchResultsSort-{id}-default-1": { ... },               // 排序选项
    ...
  },
  "pageInfo": { ... },
  "seo": { ... },
  "shared": { ... },
  "pageToken": "...",
  ...
}
```

### 1.2 getCatalogFilterValues

```
POST https://www.ozon.ru/api/composer-api.bx/_action/v2/getCatalogFilterValues

Content-Type: application/json
x-o3-app-name: dweb_client

Body:
{
  "baseLink": "/category/elektronika-15500/",
  "isOpened": "true",
  "key": "category",
  "pageType": "category"
}
```

**响应结构：**
```json
{
  "data": {
    "isOpened": true,
    "categories": [
      {
        "title": "电子产品",
        "isActive": true,
        "level": 0,
        "urlValue": "/category/elektronika-15500/"
      },
      {
        "title": "手机和智能手表",
        "level": 1,
        "urlValue": "/category/telefony-i-smart-chasy-15501/"
      },
      ...
    ]
  }
}
```

**`level` 字段含义（相对于当前页）：**

| level | 含义 |
|-------|------|
| 0 | 当前类目自身（isActive=true）或父级类目（返回上一级） |
| 1 | 当前类目的直接子类目（二级） |
| 2 | 孙级类目（三级，仅在二级页面上显示） |

**重要：** `getCatalogFilterValues` 只返回**下一级**子类目，不递归。要获取完整三级树，需要：
1. 先获取一级类目列表
2. 对每个一级类目调用 `getCatalogFilterValues` 获取二级
3. 对每个二级类目调用 `getCatalogFilterValues` 获取三级

---

## 二、类目 URL 拼接规律

### 2.1 URL 格式

```
/category/{slug}-{category_id}/
```

| 层级 | 示例 slug | category_id | 完整 URL |
|------|-----------|-------------|----------|
| 一级 | elektronika | 15500 | `/category/elektronika-15500/` |
| 二级 | telefony-i-smart-chasy | 15501 | `/category/telefony-i-smart-chasy-15501/` |
| 三级 | smartfony | 15502 | `/category/smartfony-15502/` |

**规律：** `https://www.ozon.ru/category/{俄语/英语slug}-{纯数字category_id}/`

### 2.2 类目层级关系（示例）

```
电子产品 (15500)
├── 手机和智能手表 (15501)
│   ├── 智能手机 (15502)
│   ├── 智能手机和手机配件 (15511)
│   ├── 智能手表 (15516)
│   ├── 健身手环 (15519)
│   └── 智能手表/手环表带 (15517)
├── 耳机和音频设备 (15543)
├── 笔记本电脑、平板电脑和电子阅读器 (8730)
├── 电视和视频设备 (15527)
├── 计算机组件 (15709)
├── 计算机和外围设备 (15690)
├── 游戏机和笔记本电脑 (15800)
├── 办公设备 (15770)
└── 照片和视频相机 (15623)
```

### 2.3 获取完整类目树的推荐流程

```
步骤1: 导航到 Ozon 主页，从 entrypoint API 的 catalogMenu widget 获取全部一级类目
       → catalogMenu.categories[] → {id, title, url, image, icon}

步骤2: 对每个一级类目，调用 getCatalogFilterValues 获取二级子类目
       → POST /api/composer-api.bx/_action/v2/getCatalogFilterValues
       → body: {baseLink: "/category/{slug}-{id}/", isOpened: "true", key: "category", pageType: "category"}
       → 解析响应中的 level=1 类目

步骤3: 对每个二级类目，同样调用 getCatalogFilterValues 获取三级子类目
       → 解析响应中的 level=1 类目（此时它们是三级）
```

---

## 三、筛选参数分析

### 3.1 排序 (sorting)

| sorting 值 | 含义 |
|------------|------|
| `score` | 按流行度排序（默认） |
| `new` | 按新品排序 |
| `price` | 按价格从低到高 |

URL 示例：
```
/category/elektronika-15500/?sorting=score
/category/elektronika-15500/?sorting=new
/category/elektronika-15500/?sorting=price
```

### 3.2 价格筛选 (currency_price)

**格式：** `currency_price={from}.000;{to}.000`

**说明：**
- 价格单位为**卢布 (RUB)**
- 值需要带 `.000` 三位小数后缀
- 分号 `;` 分隔 from 和 to
- minValue=1, maxValue=5500000

**URL 示例：**
```
# 价格 1₽ ~ 250₽
/category/elektronika-15500/?currency_price=1.000;250.000

# 价格 250₽ ~ 750₽
/category/elektronika-15500/?currency_price=250.000;750.000

# 价格 750₽ ~ 4000₽
/category/elektronika-15500/?currency_price=750.000;4000.000

# 价格 1000₽ ~ 5000₽ (自定义范围)
/category/elektronika-15500/?currency_price=1000.000;5000.000
```

**filter 数据结构（来自 filtersDesktop）：**
```json
{
  "type": "multipleRangesFilter",
  "key": "currency_price",
  "multipleRangesFilter": {
    "rangeFilter": {
      "title": "价格",
      "minValue": 1,
      "maxValue": 5500000,
      "hideSlider": true
    },
    "checkboxesFilter": {
      "sections": [{
        "items": [
          {"key": "1.000;250.000", "title": {"text": "到250 ₽"}},
          {"key": "250.000;750.000", "title": {"text": "250–750 ₽"}},
          {"key": "750.000;4000.000", "title": {"text": "750–4 000 ₽"}},
          {"key": "4000.000;5500000.000", "title": {"text": "4 000 ₽ 和更贵"}}
        ]
      }]
    }
  }
}
```

### 3.3 其他筛选器类型

| key | type | URL 参数格式 | 说明 |
|-----|------|-------------|------|
| `currency_price` | multipleRangesFilter | `?currency_price=1000.000;5000.000` | 价格范围（卢布） |
| `is_promo` | boolFilter | `?is_promo=1` | 促销商品 |
| `isdiscount` | boolFilter | `?isdiscount=1` | 折扣商品 |
| `is_installment` | boolFilter | `?is_installment=1` | 分期付款 |
| `brandcertified` | boolFilter | `?brandcertified=1` | 原装正品 |
| `is_official_brand_seller` | boolFilter | `?is_official_brand_seller=1` | 官方品牌店 |
| `has_points_from_reviews` | boolFilter | `?has_points_from_reviews=1` | 留评获分 |
| `delivery` | checkboxesFilter | radio 单选，key: 0/1/2/4/8 | 配送时效（不重要/今天/明天/3天/7天） |
| `brand` | checkboxesFilter | 多选 | 品牌筛选 |
| `color` | colorFilter | - | 颜色筛选 |
| `type` | checkboxesFilter | 多选 | 商品类型 |
| `seller` | checkboxesFilter | 多选 | 卖家筛选 |

> **注意：** 上述 URL 参数格式中 boolFilter 和 checkboxesFilter 的具体 URL 拼接方式需要通过浏览器实际筛选后观察 URL 变化来确认。当前分析基于 filter widget 的 JSON 结构推断。

---

## 四、翻页机制

### 4.1 infiniteVirtualPaginator

每个类目页返回固定数量商品（本测试中为 8 个/页），翻页数据在 `infiniteVirtualPaginator` widget 中：

```json
{
  "prevPage": "/category/elektronika-15500/?page=2&paginator_token=3618992&search_page_state=...&start_page_id=...",
  "nextPage": "/category/elektronika-15500/?layout_page_index=2&page=2&paginator_token=3618992&search_page_state=...&start_page_id=...",
  "size": 10,
  "fetchType": "virtualScroll",
  "layoutContainer": "default"
}
```

**翻页参数说明：**

| 参数 | 说明 |
|------|------|
| `page` | 页码（从 2 开始，第 1 页不带此参数） |
| `layout_page_index` | 布局页索引 |
| `paginator_token` | 翻页令牌（固定值，从第 1 页响应中获取） |
| `search_page_state` | 搜索状态编码（固定值） |
| `start_page_id` | 会话页面 ID（固定值） |

**翻页方式：**
1. 第一页直接用类目 URL：`/category/{slug}-{id}/`
2. 后续页从 `infiniteVirtualPaginator.nextPage` 获取完整 path
3. 将 `nextPage` 拼接到 entrypoint API：
   ```
   /api/entrypoint-api.bx/page/json/v2?url={encodeURIComponent(nextPage)}
   ```

**注意：** `paginator_token`、`search_page_state`、`start_page_id` 在**同一会话**内保持不变，只需从第一页响应中提取一次即可。

---

## 五、商品数据结构 (tileGridDesktop)

### 5.1 商品对象结构

```json
{
  "action": {
    "behavior": "BEHAVIOR_TYPE_REDIRECT",
    "link": "/product/{slug}-{sku}/",
    "params": { "target": "_blank" }
  },
  "id": "1928647892",
  "isAdult": false,
  "mainState": [
    {
      "type": "priceV2",
      "priceV2": {
        "price": [
          {"text": "2 ", "textStyle": "PRICE"},
          {"text": "₽", "textStyle": "PRICE"},
          {"text": "33 ", "textStyle": "ORIGINAL_PRICE"},
          {"text": "₽", "textStyle": "ORIGINAL_PRICE"}
        ],
        "discount": "-93%",
        "priceStyle": { "styleType": "SALE_PRICE", "gradient": {...} }
      }
    },
    {
      "type": "labelListV2",       // 标签列表（品牌名、认证标记等）
      "labelListV2": {
        "items": [
          {"type": "text", "text": {"text": "品牌名"}},
          {"type": "icon", "icon": {"icon": "ic_s_confirmed_filled_compact"}},
          {"type": "text", "text": {"text": "源语言"}}
        ]
      }
    },
    {
      "type": "textDS",            // 商品标题
      "textDS": {
        "text": "商品标题文字",
        "maxLines": 2
      },
      "id": "name"
    },
    {
      "type": "labelListV2",       // 评分和评论数
      "labelListV2": {
        "items": [
          {"type": "icon", "icon": {"icon": "ic_s_star_filled_compact"}},
          {"type": "text", "text": {"text": "4.8"}},
          {"type": "icon", "icon": {"icon": "ic_s_dialog_filled_compact"}},
          {"type": "text", "text": {"text": "评论数"}}
        ]
      }
    }
  ],
  "tileImage": {
    "items": [{ "image": { "link": "https://ir-2.ozonstatic.cn/s3/...jpg" } }]
  },
  "multiButton": {
    "ozonButton": {
      "addToCart": {
        "quantityButton": { "maxItems": 3 }   // 库存上限
      }
    }
  },
  "brandLogo": {
    "logo": "https://cdn1.ozonusercontent.com/s3/.../brand.jpg"
  },
  "rating": {
    "value": 4.8,
    "count": 12345
  }
}
```

### 5.2 商品关键字段提取

| 字段路径 | 含义 | 提取方式 |
|----------|------|----------|
| `id` / `sku` | SKU ID | `item.get("id") or item.get("sku")` |
| `action.link` | 商品详情页 URL | `item["action"]["link"]` |
| 标题 | 商品名称 | 遍历 mainState，取 `type=="textDS"` 的 `textDS.text` |
| 现价 | 当前价格 | 遍历 mainState，取 `type=="priceV2"` 的 `priceV2.price`，拼接 text 字段 |
| 折扣 | 折扣百分比 | `priceV2.discount` 如 `"-93%"` |
| 品牌名 | 标签中的品牌 | 遍历 mainState 第一个 labelListV2 的 items |
| 评分 | 用户评分 | 遍历 mainState 最后一个 labelListV2 |
| 图片 | 商品图片 URL | `tileImage.items[0].image.link` |
| 库存 | 最大可购数量 | `multiButton.ozonButton.addToCart.quantityButton.maxItems` |
| 品牌 Logo | 品牌图标 | `brandLogo.logo` |

---

## 六、数据库表设计

### 6.1 新表 `ozon_category_urls`

> **已建表并导入数据（2026-06-10 最终更新）:** 该表已创建在 `ozon_selection` 数据库中。
> - 一级类目：29 个
> - 二级类目：305 个
> - 三级类目：1966 个
> - 四级类目：2716 个
> - 总计：**5016 条记录**

```sql
CREATE TABLE IF NOT EXISTS ozon_category_urls (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    category_id BIGINT NOT NULL COMMENT 'Ozon类目ID',
    parent_id BIGINT NOT NULL DEFAULT 0 COMMENT '父级类目ID，0=顶级',
    level TINYINT NOT NULL COMMENT '层级: 1=一级, 2=二级, 3=三级',
    name_ru VARCHAR(255) NOT NULL DEFAULT '' COMMENT '类目俄语名',
    slug VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'URL slug部分',
    url VARCHAR(512) NOT NULL DEFAULT '' COMMENT '完整相对URL /category/{slug}-{id}/',
    icon VARCHAR(512) NOT NULL DEFAULT '' COMMENT '类目图标URL',
    image VARCHAR(512) NOT NULL DEFAULT '' COMMENT '类目图片URL',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_category_id (category_id),
    INDEX idx_parent_id (parent_id),
    INDEX idx_level (level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.2 字段说明

| 字段 | 来源 | 说明 |
|------|------|------|
| `category_id` | catalogMenu.id 或 categoryFilter 的 URL 中提取 | Ozon 官网类目 ID（如 15500） |
| `parent_id` | 父类目的 category_id | 用于构建层级关系 |
| `level` | categoryFilter 的 level（已转换为绝对层级 1/2/3） | 1=一级, 2=二级, 3=三级 |
| `name_ru` | catalogMenu.title 或 categoryFilter.title | 俄语原名 |
| `slug` | URL 中 `-` 之前的部分 | 如 `elektronika` |
| `url` | catalogMenu.url 或 categoryFilter.urlValue | 相对 URL |
| `icon` | catalogMenu.icon（仅一级类目有） | 类目图标标识 |
| `image` | catalogMenu.image（仅一级类目有） | 类目图片 URL |

### 6.3 与现有 `ozon_categories` 表的关系

**验证结论（2026-06-10 实测）：**

Ozon 官网类目 ID 与毛子 ERP 类目 ID **完全不一致，交集为 0**。

| 数据源 | 一级类目数 | ID 范围示例 | ID 来源 |
|--------|-----------|-------------|---------|
| Ozon 官网 | 29 个 | 6000, 6500, 7000, ..., 15500, 16500, 17777 | Ozon `catalogMenu` widget |
| 毛子 ERP (`ozon_categories`) | 26 个 | 15621031, 17027482, ..., 99999999 | 毛子榜单 API `cate1_id` |

两套 ID 体系完全不同，无法关联。`ozon_category_urls` 必须作为独立表存在，专门用于 Ozon 官网类目页面 URL 的拼接和导航。

**一级类目 ID 对比：**
- **Ozon 官网（29 个）：** 6000, 6500, 7000, 7500, 7697, 8000, 8500, 9000, 9200, 9700, 10500, 11000, 12300, 13100, 13300, 13500, 14500, 14572, 15000, 15500, 16500, 17777, 18000, 32056, 33332, 35659, 37234, 39803, 50001
- **毛子 ERP（26 个）：** 15621031, 15621032, 15621042, 17027482, 17027484, ..., 92130764, 99999999, 200001482

---

## 七、采集流程设计

```
┌──────────────────────────────────────────────────┐
│  初始化：浏览器 CDP 连接 + Ozon 登录态验证        │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│  步骤1: 获取一级类目列表                          │
│  → 导航到 Ozon 主页                              │
│  → entrypoint API → catalogMenu widget           │
│  → 提取 categories[] → 写入 ozon_category_urls   │
│     (level=1, parent_id=0)                       │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│  步骤2: 获取二级类目                              │
│  → 对每个一级类目，POST getCatalogFilterValues   │
│  → baseLink = 一级类目 URL                       │
│  → 提取 level=1 的 categories → 写入数据库       │
│     (level=2, parent_id=一级category_id)         │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│  步骤3: 获取三级类目                              │
│  → 对每个二级类目，POST getCatalogFilterValues   │
│  → baseLink = 二级类目 URL                       │
│  → 提取 level=1 的 categories → 写入数据库       │
│     (level=3, parent_id=二级category_id)         │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│  步骤4: 采集类目商品                              │
│  → 遍历三级类目（或二级类目），对每个类目：       │
│    1. entrypoint API 获取第1页                   │
│    2. 从 tileGridDesktop 提取商品列表            │
│    3. 从 infiniteVirtualPaginator 获取 nextPage  │
│    4. 翻页循环，直到没有 nextPage                │
│  → 商品数据写入 seed_pool_skus 或新表            │
└──────────────────────────────────────────────────┘
```

---

## 八、关键发现与注意事项

1. **无需单独 Token：** Ozon entrypoint API 不需要 Authorization header，只需浏览器携带已登录的 Cookie（`credentials: 'include'`）

2. **同源策略：** 必须在 `ozon.ru` 页面中注入 JS 调用 fetch，因为需要同源 Cookie

3. **catalogMenu 天然有完整一级类目：** 不需要通过 categoryChildV3 API（此 API 实测返回 500）

4. **getCatalogFilterValues 不递归：** 每次调用只返回下一级子类目，需要遍历二级类目再次调用才能获取三级

5. **翻页 token 可复用：** `paginator_token`、`search_page_state`、`start_page_id` 在同一会话内不变

6. **每页商品数不固定：** 实测 8 个/页，但可能因类目不同而变化，不应硬编码

7. **价格筛选格式固定：** `currency_price={from}.000;{to}.000`（`.000` 后缀可能可选，但保险起见保留）
