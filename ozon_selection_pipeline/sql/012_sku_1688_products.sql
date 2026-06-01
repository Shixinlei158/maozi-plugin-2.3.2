-- 012: 新建 sku_1688_products 表，支持 SKU 一对多图搜结果
-- 保留 sku_products 旧列的写入兼容

CREATE TABLE IF NOT EXISTS `sku_1688_products` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `sku`        VARCHAR(32)  NOT NULL COMMENT '关联SKU',
  `item_id`    VARCHAR(64)  NULL     COMMENT '1688商品ID',
  `image_url`  VARCHAR(2048) NULL     COMMENT '商品图片链接',
  `detail_url` VARCHAR(2048) NULL     COMMENT '1688商品详情链接',
  `rank_pos`   INT          NOT NULL DEFAULT 0 COMMENT '搜索结果排名(1-N)',
  `raw_json`   JSON         NULL     COMMENT '单条商品原始响应JSON',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_sku` (`sku`),
  CONSTRAINT `fk_sku_1688_products_sku`
    FOREIGN KEY (`sku`) REFERENCES `sku_products`(`sku`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='1688图搜结果明细：每行一个匹配商品，支持SKU一对多';
