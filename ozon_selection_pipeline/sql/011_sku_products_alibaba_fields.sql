-- 补全 sku_products Alibaba 系列字段
ALTER TABLE sku_products ADD COLUMN alibaba_image_links json NULL;
ALTER TABLE sku_products ADD COLUMN alibaba_detail_urls json NULL;
ALTER TABLE sku_products ADD COLUMN alibaba_image_search_response json NULL;
