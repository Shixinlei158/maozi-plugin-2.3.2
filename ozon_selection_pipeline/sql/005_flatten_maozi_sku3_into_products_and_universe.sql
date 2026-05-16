USE `ozon_selection`;

ALTER TABLE `sku_universe`
  ADD COLUMN `rfbs_leq_1500` DECIMAL(10,4) NULL AFTER `category_ids`;

ALTER TABLE `sku_universe`
  ADD COLUMN `rfbs_leq_5000` DECIMAL(10,4) NULL AFTER `rfbs_leq_1500`;

ALTER TABLE `sku_universe`
  ADD COLUMN `rfbs_gt_5000` DECIMAL(10,4) NULL AFTER `rfbs_leq_5000`;

ALTER TABLE `sku_universe`
  ADD COLUMN `fbp_leq_1500` DECIMAL(10,4) NULL AFTER `rfbs_gt_5000`;

ALTER TABLE `sku_universe`
  ADD COLUMN `fbp_leq_5000` DECIMAL(10,4) NULL AFTER `fbp_leq_1500`;

ALTER TABLE `sku_universe`
  ADD COLUMN `fbp_gt_5000` DECIMAL(10,4) NULL AFTER `fbp_leq_5000`;

ALTER TABLE `sku_universe`
  ADD COLUMN `maozi_fields_zh_json` JSON NULL AFTER `seed_rule_reason`;

ALTER TABLE `sku_products`
  ADD COLUMN `rfbs_leq_1500` DECIMAL(10,4) NULL AFTER `image_urls`;

ALTER TABLE `sku_products`
  ADD COLUMN `rfbs_leq_5000` DECIMAL(10,4) NULL AFTER `rfbs_leq_1500`;

ALTER TABLE `sku_products`
  ADD COLUMN `rfbs_gt_5000` DECIMAL(10,4) NULL AFTER `rfbs_leq_5000`;

ALTER TABLE `sku_products`
  ADD COLUMN `fbp_leq_1500` DECIMAL(10,4) NULL AFTER `rfbs_gt_5000`;

ALTER TABLE `sku_products`
  ADD COLUMN `fbp_leq_5000` DECIMAL(10,4) NULL AFTER `fbp_leq_1500`;

ALTER TABLE `sku_products`
  ADD COLUMN `fbp_gt_5000` DECIMAL(10,4) NULL AFTER `fbp_leq_5000`;

ALTER TABLE `sku_products`
  ADD COLUMN `sold_count` INT NULL AFTER `fbp_gt_5000`;

ALTER TABLE `sku_products`
  ADD COLUMN `sold_sum_text` VARCHAR(128) NULL AFTER `sold_count`;

ALTER TABLE `sku_products`
  ADD COLUMN `sold_sum_rub` DECIMAL(18,4) NULL AFTER `sold_sum_text`;

ALTER TABLE `sku_products`
  ADD COLUMN `sold_sum_cny` DECIMAL(18,4) NULL AFTER `sold_sum_rub`;

ALTER TABLE `sku_products`
  ADD COLUMN `avg_orders_on_acc_days` DECIMAL(18,4) NULL AFTER `sold_sum_cny`;

ALTER TABLE `sku_products`
  ADD COLUMN `avg_gmv_on_acc_days` DECIMAL(18,4) NULL AFTER `avg_orders_on_acc_days`;

ALTER TABLE `sku_products`
  ADD COLUMN `avg_gmv_on_acc_days_cny` DECIMAL(18,4) NULL AFTER `avg_gmv_on_acc_days`;

ALTER TABLE `sku_products`
  ADD COLUMN `sales_dynamics` DECIMAL(18,4) NULL AFTER `avg_gmv_on_acc_days_cny`;

ALTER TABLE `sku_products`
  ADD COLUMN `drr` DECIMAL(10,4) NULL AFTER `sales_dynamics`;

ALTER TABLE `sku_products`
  ADD COLUMN `days_in_promo` INT NULL AFTER `drr`;

ALTER TABLE `sku_products`
  ADD COLUMN `discount` DECIMAL(10,4) NULL AFTER `days_in_promo`;

ALTER TABLE `sku_products`
  ADD COLUMN `promo_revenue_share` DECIMAL(10,4) NULL AFTER `discount`;

ALTER TABLE `sku_products`
  ADD COLUMN `days_with_trafarets` INT NULL AFTER `promo_revenue_share`;

ALTER TABLE `sku_products`
  ADD COLUMN `qty_view_pdp` INT NULL AFTER `days_with_trafarets`;

ALTER TABLE `sku_products`
  ADD COLUMN `session_count_search` INT NULL AFTER `qty_view_pdp`;

ALTER TABLE `sku_products`
  ADD COLUMN `conv_to_cart_pdp` DECIMAL(10,4) NULL AFTER `session_count_search`;

ALTER TABLE `sku_products`
  ADD COLUMN `conv_to_cart_search` DECIMAL(10,4) NULL AFTER `conv_to_cart_pdp`;

ALTER TABLE `sku_products`
  ADD COLUMN `conv_view_to_order` DECIMAL(10,4) NULL AFTER `conv_to_cart_search`;

ALTER TABLE `sku_products`
  ADD COLUMN `sales_schema` VARCHAR(128) NULL AFTER `conv_view_to_order`;

ALTER TABLE `sku_products`
  ADD COLUMN `nullable_redemption_rate` DECIMAL(10,4) NULL AFTER `sales_schema`;

ALTER TABLE `sku_products`
  ADD COLUMN `custom_click_rate_text` VARCHAR(64) NULL AFTER `nullable_redemption_rate`;

ALTER TABLE `sku_products`
  ADD COLUMN `custom_click_rate` DECIMAL(10,4) NULL AFTER `custom_click_rate_text`;

ALTER TABLE `sku_products`
  ADD COLUMN `custom_volume_text` VARCHAR(128) NULL AFTER `custom_click_rate`;

ALTER TABLE `sku_products`
  ADD COLUMN `custom_weight_text` VARCHAR(64) NULL AFTER `custom_volume_text`;

ALTER TABLE `sku_products`
  ADD COLUMN `custom_weight_g` DECIMAL(18,4) NULL AFTER `custom_weight_text`;

ALTER TABLE `sku_products`
  ADD COLUMN `nullable_create_date_text` VARCHAR(128) NULL AFTER `custom_weight_g`;

ALTER TABLE `sku_products`
  ADD COLUMN `create_days` INT NULL AFTER `nullable_create_date_text`;

ALTER TABLE `sku_products`
  ADD COLUMN `status_update_sales` TINYINT(1) NOT NULL DEFAULT 0 AFTER `create_days`;

ALTER TABLE `sku_products`
  ADD COLUMN `status_update_variant` TINYINT(1) NOT NULL DEFAULT 0 AFTER `status_update_sales`;

ALTER TABLE `sku_products`
  ADD COLUMN `status_version` INT NULL AFTER `status_update_variant`;

ALTER TABLE `sku_products`
  ADD COLUMN `maozi_fields_zh_json` JSON NULL AFTER `status_version`;

ALTER TABLE `sku_products`
  ADD COLUMN `raw_maozi_json` JSON NULL AFTER `maozi_fields_zh_json`;

ALTER TABLE `sku_products`
  ADD COLUMN `maozi_collected_at` DATETIME NULL AFTER `raw_frontend_json`;

UPDATE `sku_universe` u
JOIN `sku_plugin_metrics` m ON m.`sku` = u.`sku`
SET
  u.`rfbs_leq_1500` = COALESCE(u.`rfbs_leq_1500`, m.`rfbs_leq_1500`),
  u.`rfbs_leq_5000` = COALESCE(u.`rfbs_leq_5000`, m.`rfbs_leq_5000`),
  u.`rfbs_gt_5000` = COALESCE(u.`rfbs_gt_5000`, m.`rfbs_gt_5000`),
  u.`fbp_leq_1500` = COALESCE(u.`fbp_leq_1500`, m.`fbp_leq_1500`),
  u.`fbp_leq_5000` = COALESCE(u.`fbp_leq_5000`, m.`fbp_leq_5000`),
  u.`fbp_gt_5000` = COALESCE(u.`fbp_gt_5000`, m.`fbp_gt_5000`),
  u.`last_maozi_collected_at` = COALESCE(u.`last_maozi_collected_at`, m.`collected_at`);

INSERT INTO `sku_products`
  (`sku`, `variant_id`, `product_url`, `title`, `brand`, `category`, `category_ids`,
   `price`, `currency`, `main_image_url`,
   `rfbs_leq_1500`, `rfbs_leq_5000`, `rfbs_gt_5000`, `fbp_leq_1500`, `fbp_leq_5000`, `fbp_gt_5000`,
   `sold_count`, `sold_sum_text`, `sold_sum_rub`, `sold_sum_cny`, `avg_orders_on_acc_days`,
   `avg_gmv_on_acc_days`, `avg_gmv_on_acc_days_cny`, `sales_dynamics`, `drr`, `days_in_promo`,
   `discount`, `promo_revenue_share`, `days_with_trafarets`, `qty_view_pdp`, `session_count_search`,
   `conv_to_cart_pdp`, `conv_to_cart_search`, `conv_view_to_order`, `sales_schema`,
   `nullable_redemption_rate`, `custom_click_rate_text`, `custom_click_rate`, `custom_volume_text`,
   `custom_weight_text`, `custom_weight_g`, `nullable_create_date_text`, `create_days`,
   `status_update_sales`, `status_update_variant`, `status_version`, `maozi_fields_zh_json`, `raw_maozi_json`,
   `raw_frontend_json`, `maozi_collected_at`, `first_seen_at`, `last_seen_at`)
SELECT
  u.`sku`, u.`variant_id`, u.`product_url`, u.`title`, u.`brand`, u.`category`, u.`category_ids`,
  u.`price_amount`, u.`price_currency`, u.`main_image_url`,
  u.`rfbs_leq_1500`, u.`rfbs_leq_5000`, u.`rfbs_gt_5000`, u.`fbp_leq_1500`, u.`fbp_leq_5000`, u.`fbp_gt_5000`,
  u.`sold_count`, u.`sold_sum_text`, u.`sold_sum_rub`, u.`sold_sum_cny`, u.`avg_orders_on_acc_days`,
  u.`avg_gmv_on_acc_days`, u.`avg_gmv_on_acc_days_cny`, u.`sales_dynamics`, u.`drr`, u.`days_in_promo`,
  u.`discount`, u.`promo_revenue_share`, u.`days_with_trafarets`, u.`qty_view_pdp`, u.`session_count_search`,
  u.`conv_to_cart_pdp`, u.`conv_to_cart_search`, u.`conv_view_to_order`, u.`sales_schema`,
  u.`nullable_redemption_rate`, u.`custom_click_rate_text`, u.`custom_click_rate`, u.`custom_volume_text`,
  u.`custom_weight_text`, u.`custom_weight_g`, u.`nullable_create_date_text`, u.`create_days`,
  COALESCE(u.`status_update_sales`, 0), COALESCE(u.`status_update_variant`, 0), u.`status_version`, u.`maozi_fields_zh_json`, u.`maozi_raw_json`,
  u.`product_raw_json`, u.`last_maozi_collected_at`, u.`first_seen_at`, u.`last_seen_at`
FROM `sku_universe` u
JOIN `seed_skus` s
  ON s.`sku` = u.`sku`
 AND s.`status` = 'qualified'
WHERE u.`last_maozi_collected_at` IS NOT NULL
ON DUPLICATE KEY UPDATE
  `variant_id` = COALESCE(VALUES(`variant_id`), `sku_products`.`variant_id`),
  `product_url` = COALESCE(VALUES(`product_url`), `sku_products`.`product_url`),
  `title` = COALESCE(VALUES(`title`), `sku_products`.`title`),
  `brand` = COALESCE(VALUES(`brand`), `sku_products`.`brand`),
  `category` = COALESCE(VALUES(`category`), `sku_products`.`category`),
  `category_ids` = COALESCE(VALUES(`category_ids`), `sku_products`.`category_ids`),
  `price` = COALESCE(VALUES(`price`), `sku_products`.`price`),
  `currency` = COALESCE(VALUES(`currency`), `sku_products`.`currency`),
  `main_image_url` = COALESCE(VALUES(`main_image_url`), `sku_products`.`main_image_url`),
  `rfbs_leq_1500` = COALESCE(VALUES(`rfbs_leq_1500`), `sku_products`.`rfbs_leq_1500`),
  `rfbs_leq_5000` = COALESCE(VALUES(`rfbs_leq_5000`), `sku_products`.`rfbs_leq_5000`),
  `rfbs_gt_5000` = COALESCE(VALUES(`rfbs_gt_5000`), `sku_products`.`rfbs_gt_5000`),
  `fbp_leq_1500` = COALESCE(VALUES(`fbp_leq_1500`), `sku_products`.`fbp_leq_1500`),
  `fbp_leq_5000` = COALESCE(VALUES(`fbp_leq_5000`), `sku_products`.`fbp_leq_5000`),
  `fbp_gt_5000` = COALESCE(VALUES(`fbp_gt_5000`), `sku_products`.`fbp_gt_5000`),
  `sold_count` = COALESCE(VALUES(`sold_count`), `sku_products`.`sold_count`),
  `sold_sum_text` = COALESCE(VALUES(`sold_sum_text`), `sku_products`.`sold_sum_text`),
  `sold_sum_rub` = COALESCE(VALUES(`sold_sum_rub`), `sku_products`.`sold_sum_rub`),
  `sold_sum_cny` = COALESCE(VALUES(`sold_sum_cny`), `sku_products`.`sold_sum_cny`),
  `avg_orders_on_acc_days` = COALESCE(VALUES(`avg_orders_on_acc_days`), `sku_products`.`avg_orders_on_acc_days`),
  `avg_gmv_on_acc_days` = COALESCE(VALUES(`avg_gmv_on_acc_days`), `sku_products`.`avg_gmv_on_acc_days`),
  `avg_gmv_on_acc_days_cny` = COALESCE(VALUES(`avg_gmv_on_acc_days_cny`), `sku_products`.`avg_gmv_on_acc_days_cny`),
  `sales_dynamics` = COALESCE(VALUES(`sales_dynamics`), `sku_products`.`sales_dynamics`),
  `drr` = COALESCE(VALUES(`drr`), `sku_products`.`drr`),
  `days_in_promo` = COALESCE(VALUES(`days_in_promo`), `sku_products`.`days_in_promo`),
  `discount` = COALESCE(VALUES(`discount`), `sku_products`.`discount`),
  `promo_revenue_share` = COALESCE(VALUES(`promo_revenue_share`), `sku_products`.`promo_revenue_share`),
  `days_with_trafarets` = COALESCE(VALUES(`days_with_trafarets`), `sku_products`.`days_with_trafarets`),
  `qty_view_pdp` = COALESCE(VALUES(`qty_view_pdp`), `sku_products`.`qty_view_pdp`),
  `session_count_search` = COALESCE(VALUES(`session_count_search`), `sku_products`.`session_count_search`),
  `conv_to_cart_pdp` = COALESCE(VALUES(`conv_to_cart_pdp`), `sku_products`.`conv_to_cart_pdp`),
  `conv_to_cart_search` = COALESCE(VALUES(`conv_to_cart_search`), `sku_products`.`conv_to_cart_search`),
  `conv_view_to_order` = COALESCE(VALUES(`conv_view_to_order`), `sku_products`.`conv_view_to_order`),
  `sales_schema` = COALESCE(VALUES(`sales_schema`), `sku_products`.`sales_schema`),
  `nullable_redemption_rate` = COALESCE(VALUES(`nullable_redemption_rate`), `sku_products`.`nullable_redemption_rate`),
  `custom_click_rate_text` = COALESCE(VALUES(`custom_click_rate_text`), `sku_products`.`custom_click_rate_text`),
  `custom_click_rate` = COALESCE(VALUES(`custom_click_rate`), `sku_products`.`custom_click_rate`),
  `custom_volume_text` = COALESCE(VALUES(`custom_volume_text`), `sku_products`.`custom_volume_text`),
  `custom_weight_text` = COALESCE(VALUES(`custom_weight_text`), `sku_products`.`custom_weight_text`),
  `custom_weight_g` = COALESCE(VALUES(`custom_weight_g`), `sku_products`.`custom_weight_g`),
  `nullable_create_date_text` = COALESCE(VALUES(`nullable_create_date_text`), `sku_products`.`nullable_create_date_text`),
  `create_days` = COALESCE(VALUES(`create_days`), `sku_products`.`create_days`),
  `status_update_sales` = VALUES(`status_update_sales`),
  `status_update_variant` = VALUES(`status_update_variant`),
  `status_version` = COALESCE(VALUES(`status_version`), `sku_products`.`status_version`),
  `maozi_fields_zh_json` = COALESCE(VALUES(`maozi_fields_zh_json`), `sku_products`.`maozi_fields_zh_json`),
  `raw_maozi_json` = COALESCE(VALUES(`raw_maozi_json`), `sku_products`.`raw_maozi_json`),
  `raw_frontend_json` = COALESCE(VALUES(`raw_frontend_json`), `sku_products`.`raw_frontend_json`),
  `maozi_collected_at` = COALESCE(VALUES(`maozi_collected_at`), `sku_products`.`maozi_collected_at`),
  `last_seen_at` = CURRENT_TIMESTAMP;
