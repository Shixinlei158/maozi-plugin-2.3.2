-- ============================================================
-- 毛子采集 Mini 版 - 数据库初始化
-- 保留四张核心表：
--   1. ozon_categories  - 类目树
--   2. seed_pool_skus   - 榜单种子池
--   3. seller_shops     - 卖家表（含来源sku和来源表）
--   4. sku_products     - 合格SKU表
-- ============================================================

CREATE DATABASE IF NOT EXISTS `ozon_selection`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `ozon_selection`;

-- ============================================================
-- 1. 类目树表
-- ============================================================
CREATE TABLE IF NOT EXISTS `ozon_categories` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `category_id`     BIGINT       NOT NULL COMMENT 'Ozon类目ID',
    `name_zh`         VARCHAR(255) NOT NULL COMMENT '中文类目名称',
    `name_en`         VARCHAR(255) DEFAULT ''  COMMENT '英文类目名称',
    `parent_id`       BIGINT       DEFAULT 0  COMMENT '父级category_id',
    `level`           TINYINT      NOT NULL DEFAULT 1 COMMENT '层级 1=一级 2=二级 3=三级',
    `sort_order`      INT          DEFAULT 0,
    `created_at`      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    `updated_at`      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_category_id` (`category_id`),
    KEY `idx_parent` (`parent_id`),
    KEY `idx_level` (`level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Ozon类目树表';

-- ============================================================
-- 2. 种子池表 (榜单采集后保存合格的种子)
-- ============================================================
CREATE TABLE IF NOT EXISTS `seed_pool_skus` (
    `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `source_type`     VARCHAR(32)     NOT NULL DEFAULT 'top_list' COMMENT '来源类型',
    `query_key`       CHAR(40)        NOT NULL COMMENT '查询指纹',
    `source_run_id`   BIGINT UNSIGNED NULL,
    `sku`             VARCHAR(32)     NOT NULL,
    `page_no`         INT             NOT NULL,
    `page_rank`       INT             NOT NULL,
    `name`            VARCHAR(512)    NULL,
    `brand`           VARCHAR(255)    NULL,
    `link`            VARCHAR(1024)   NULL,
    `photo`           VARCHAR(2048)   NULL,
    `cate1`           VARCHAR(255)    NULL,
    `cate2`           VARCHAR(255)    NULL,
    `cate3`           VARCHAR(255)    NULL,
    `sold_count`      INT             NULL,
    `sold_sum`        DECIMAL(18,4)   NULL,
    `avg_price`       DECIMAL(18,4)   NULL,
    `sales_dynamics`  DECIMAL(18,4)   NULL,
    `conv_to_cart_pdp`   DECIMAL(18,4) NULL,
    `conv_to_cart_search` DECIMAL(18,4) NULL,
    `conv_view_to_order` DECIMAL(18,4) NULL,
    `qty_view_pdp`    INT             NULL,
    `views`           INT             NULL,
    `avg_delivery_days` DECIMAL(18,4) NULL,
    `volume`          DECIMAL(18,4)   NULL,
    `weight`          DECIMAL(18,4)   NULL,
    `seller_id`       VARCHAR(64)     NULL,
    `sales_schema`    VARCHAR(64)     NULL,
    `is_china`        TINYINT(1)      NOT NULL DEFAULT 0,
    `blocked_by_seller` TINYINT(1)    NOT NULL DEFAULT 0,
    `nullable_create_date` DATE       NULL,
    `upstream_update_time` DATE       NULL,
    `snapshot_hash`   CHAR(40)        NOT NULL,
    `last_processed_snapshot_hash` CHAR(40) NULL,
    `last_process_status`   VARCHAR(32) NULL COMMENT 'pending/selected/qualified/rejected/failed',
    `last_process_reason`   VARCHAR(512) NULL,
    `last_offer_count`      INT NULL,
    `last_selected_at`      DATETIME NULL,
    `last_processed_at`     DATETIME NULL,
    `raw_json`              JSON NULL,
    `first_seen_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `last_seen_at`    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `created_at`      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_seed_pool_source_query_sku` (`source_type`, `query_key`, `sku`),
    KEY `idx_seed_pool_status` (`source_type`, `query_key`, `last_process_status`, `last_processed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='榜单种子池';

-- ============================================================
-- 3. 卖家表 (增加来源sku和来源表列)
-- ============================================================
CREATE TABLE IF NOT EXISTS `seller_shops` (
    `seller_key`         CHAR(40)        NOT NULL COMMENT '卖家URL的SHA1',
    `name`               VARCHAR(255)    NULL,
    `home_url`           VARCHAR(1024)   NOT NULL,
    `logo_url`           VARCHAR(2048)   NULL,
    `source_sku`         VARCHAR(32)     NULL COMMENT '来源SKU（从哪个商品发现的该卖家）',
    `source_table`       VARCHAR(64)     NULL COMMENT '来源表（如seed_pool_skus, sku_products）',
    `last_collected_at`  DATETIME        NULL COMMENT '最近采集时间',
    `next_collect_after` DATETIME        NULL COMMENT '下次可采集时间（用于冻结控制）',
    `qualified_sku_count` INT            NOT NULL DEFAULT 0 COMMENT '最近一次采集的达标SKU数',
    `raw_json`           JSON            NULL,
    `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`seller_key`),
    UNIQUE KEY `uk_seller_home_url` (`home_url`(512)),
    KEY `idx_seller_next_collect` (`next_collect_after`),
    KEY `idx_seller_last_collected` (`last_collected_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='卖家店铺表';

-- ============================================================
-- 4. 合格SKU表
-- ============================================================
CREATE TABLE IF NOT EXISTS `sku_products` (
    `sku`              VARCHAR(32)    NOT NULL,
    `variant_id`       VARCHAR(32)    NULL,
    `product_url`      VARCHAR(1024)  NULL,
    `title`            VARCHAR(512)   NULL,
    `brand`            VARCHAR(255)   NULL,
    `category`         VARCHAR(512)   NULL,
    `category_ids`     JSON           NULL,
    `price`            DECIMAL(18,4)  NULL,
    `original_price`   DECIMAL(18,4)  NULL,
    `card_price`       DECIMAL(18,4)  NULL,
    `currency`         VARCHAR(16)    NULL,
    `main_image_url`   VARCHAR(2048)  NULL,
    `image_urls`       JSON           NULL,
    `rfbs_leq_1500`    DECIMAL(10,4)  NULL,
    `rfbs_leq_5000`    DECIMAL(10,4)  NULL,
    `rfbs_gt_5000`     DECIMAL(10,4)  NULL,
    `fbp_leq_1500`     DECIMAL(10,4)  NULL,
    `fbp_leq_5000`     DECIMAL(10,4)  NULL,
    `fbp_gt_5000`      DECIMAL(10,4)  NULL,
    `sold_count`       INT            NULL,
    `sold_sum_text`    VARCHAR(128)   NULL,
    `sold_sum_rub`     DECIMAL(18,4)  NULL,
    `sold_sum_cny`     DECIMAL(18,4)  NULL,
    `avg_orders_on_acc_days`  DECIMAL(18,4) NULL,
    `avg_gmv_on_acc_days`     DECIMAL(18,4) NULL,
    `avg_gmv_on_acc_days_cny` DECIMAL(18,4) NULL,
    `sales_dynamics`   DECIMAL(18,4)  NULL,
    `drr`              DECIMAL(10,4)  NULL,
    `days_in_promo`    INT            NULL,
    `discount`         DECIMAL(10,4)  NULL,
    `promo_revenue_share` DECIMAL(10,4) NULL,
    `days_with_trafarets` INT         NULL,
    `qty_view_pdp`     INT            NULL,
    `session_count_search` INT        NULL,
    `conv_to_cart_pdp`    DECIMAL(10,4) NULL,
    `conv_to_cart_search` DECIMAL(10,4) NULL,
    `conv_view_to_order`  DECIMAL(10,4) NULL,
    `sales_schema`     VARCHAR(128)   NULL,
    `nullable_redemption_rate` DECIMAL(10,4) NULL,
    `custom_click_rate_text`  VARCHAR(64) NULL,
    `custom_click_rate`       DECIMAL(10,4) NULL,
    `custom_volume_text`      VARCHAR(128) NULL,
    `custom_weight_text`      VARCHAR(64) NULL,
    `custom_weight_g`         DECIMAL(18,4) NULL,
    `nullable_create_date_text` VARCHAR(128) NULL,
    `create_days`            INT NULL,
    `status_update_sales`    TINYINT(1) NOT NULL DEFAULT 0,
    `status_update_variant`  TINYINT(1) NOT NULL DEFAULT 0,
    `status_version`         INT NULL,
    `seller_rating`          VARCHAR(32) NULL COMMENT '卖家评分',
    `seller_review_count`    INT NULL    COMMENT '卖家评价数',
    `stock_max`              INT NULL    COMMENT '最大库存',
    `stock_label`            VARCHAR(64) NULL COMMENT '库存标签',
    `brand_logo_url`         VARCHAR(2048) NULL,
    `badges`                 JSON NULL,
    `delivery_hint`          VARCHAR(128) NULL,
    `raw_seller_home_json`   JSON NULL,
    `maozi_fields_zh_json`   JSON NULL,
    `raw_maozi_json`         JSON NULL,
    `raw_frontend_json`      JSON NULL,
    `source_sku`             VARCHAR(32) NULL COMMENT '来源SKU',
    `source_table`           VARCHAR(64) NULL COMMENT '来源表',
    `maozi_collected_at`     DATETIME NULL,
    `first_seen_at`          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `last_seen_at`           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `created_at`             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`sku`),
    KEY `idx_product_category` (`category`(128)),
    KEY `idx_product_brand` (`brand`),
    KEY `idx_product_sold_count` (`sold_count`),
    KEY `idx_product_create_days` (`create_days`),
    KEY `idx_product_weight_g` (`custom_weight_g`),
    KEY `idx_product_sales_schema` (`sales_schema`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='合格SKU产品表';

-- ============================================================
-- 5. 采集断点表 (v2.3.2 新增，断点续采)
-- ============================================================
CREATE TABLE IF NOT EXISTS `collection_checkpoint` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `query_key`       CHAR(40)        NOT NULL COMMENT '过滤条件指纹(含类目ID)',
    `category_label`  VARCHAR(255)    NOT NULL COMMENT '类目标签(中文名或all)',
    `last_page_completed` INT         NOT NULL DEFAULT 0 COMMENT '最后完成的页码',
    `total_pages_target`  INT         NOT NULL DEFAULT 0 COMMENT '目标总页数',
    `items_collected` INT             NOT NULL DEFAULT 0 COMMENT '该类目已采集的种子数',
    `status`          VARCHAR(32)     NOT NULL DEFAULT 'in_progress' COMMENT 'in_progress/completed',
    `first_seen_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_query_key` (`query_key`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='采集断点进度表';

-- ============================================================
-- 迁移：增量添加缺失列（幂等，重复执行安全）
-- ============================================================

-- seller_shops.qualified_sku_count (v2.3.2 新增)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'seller_shops' AND COLUMN_NAME = 'qualified_sku_count');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `seller_shops` ADD COLUMN `qualified_sku_count` INT NOT NULL DEFAULT 0 COMMENT ''最近一次采集的达标SKU数''',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- seller_shops.source_sku (v2.3.2 新增)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'seller_shops' AND COLUMN_NAME = 'source_sku');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `seller_shops` ADD COLUMN `source_sku` VARCHAR(32) NULL COMMENT ''来源SKU'' AFTER `logo_url`',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- seller_shops.source_table (v2.3.2 新增)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'seller_shops' AND COLUMN_NAME = 'source_table');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `seller_shops` ADD COLUMN `source_table` VARCHAR(64) NULL COMMENT ''来源表'' AFTER `source_sku`',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- sku_products.source_sku (v2.3.2 新增)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sku_products' AND COLUMN_NAME = 'source_sku');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `sku_products` ADD COLUMN `source_sku` VARCHAR(32) NULL COMMENT ''来源SKU'' AFTER `raw_frontend_json`',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- sku_products.source_table (v2.3.2 新增)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sku_products' AND COLUMN_NAME = 'source_table');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `sku_products` ADD COLUMN `source_table` VARCHAR(64) NULL COMMENT ''来源表'' AFTER `source_sku`',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
