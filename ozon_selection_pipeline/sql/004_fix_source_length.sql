USE ozon_selection;
ALTER TABLE seed_skus MODIFY COLUMN source VARCHAR(512) NOT NULL DEFAULT 'manual';
