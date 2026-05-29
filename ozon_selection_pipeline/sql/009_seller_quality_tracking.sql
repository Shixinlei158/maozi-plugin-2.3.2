ALTER TABLE seller_shops
  ADD COLUMN last_qualified_count INT DEFAULT NULL COMMENT '最近一次采集产出的合格SKU数',
  ADD COLUMN total_collect_attempts INT NOT NULL DEFAULT 0 COMMENT '累计采集次数';

-- 存量数据：已采集过的标记为至少尝试过1次
UPDATE seller_shops SET total_collect_attempts = 1 WHERE last_collected_at IS NOT NULL;
