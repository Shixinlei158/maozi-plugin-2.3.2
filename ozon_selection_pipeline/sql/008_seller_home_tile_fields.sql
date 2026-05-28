USE `ozon_selection`;

-- 从卖家主页 tile JSON 中解析出的高价值字段
ALTER TABLE `sku_products`
  ADD COLUMN `seller_rating` DECIMAL(3,2) NULL AFTER `raw_frontend_json`;

ALTER TABLE `sku_products`
  ADD COLUMN `seller_review_count` INT NULL AFTER `seller_rating`;

ALTER TABLE `sku_products`
  ADD COLUMN `stock_max` INT NULL AFTER `seller_review_count`;

ALTER TABLE `sku_products`
  ADD COLUMN `stock_label` VARCHAR(64) NULL AFTER `stock_max`;

ALTER TABLE `sku_products`
  ADD COLUMN `brand_logo_url` VARCHAR(2048) NULL AFTER `stock_label`;

ALTER TABLE `sku_products`
  ADD COLUMN `badges` JSON NULL AFTER `brand_logo_url`;

ALTER TABLE `sku_products`
  ADD COLUMN `delivery_hint` VARCHAR(64) NULL AFTER `badges`;

ALTER TABLE `sku_products`
  ADD COLUMN `raw_seller_home_json` JSON NULL AFTER `delivery_hint`;
