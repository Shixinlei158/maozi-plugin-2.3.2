-- 010_ozon_categories.sql
-- 创建 Ozon 类目树表，记录各级类目的递归父子关系
-- 用于支持 top-list API 的 category1/category2/category3 过滤查询

-- 类目树表（自引用多级树结构，parent_id 指向父级 category_id）
CREATE TABLE IF NOT EXISTS ozon_categories (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    category_id     BIGINT       NOT NULL COMMENT 'Ozon类目ID（cate1_id/cate2_id/cate3_id）',
    name_zh         VARCHAR(255) NOT NULL COMMENT '中文类目名称',
    name_en         VARCHAR(255) DEFAULT ''  COMMENT '英文类目名称',
    parent_id       BIGINT       DEFAULT 0  COMMENT '父级category_id，0=一级类目',
    level           TINYINT      NOT NULL DEFAULT 1 COMMENT '层级 1=一级 2=二级 3=三级',
    sort_order      INT          DEFAULT 0  COMMENT '同层级排序',
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_category_id (category_id),
    KEY idx_parent (parent_id),
    KEY idx_level (level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Ozon类目树表（自引用多级递归树）';


-- 扁平化视图：将三级类目树展开为 cate1/cate2/cate3 三列，方便直接按级查询
CREATE OR REPLACE VIEW ozon_category_paths AS
SELECT
    c1.category_id AS cate1_id,
    c1.name_zh     AS cate1_name_zh,
    c1.name_en     AS cate1_name_en,
    c2.category_id AS cate2_id,
    c2.name_zh     AS cate2_name_zh,
    c2.name_en     AS cate2_name_en,
    c3.category_id AS cate3_id,
    c3.name_zh     AS cate3_name_zh,
    c3.name_en     AS cate3_name_en,
    CONCAT(
        COALESCE(c1.name_zh, ''),
        CASE WHEN c2.name_zh != '' THEN CONCAT('/', c2.name_zh) ELSE '' END,
        CASE WHEN c3.name_zh != '' THEN CONCAT('/', c3.name_zh) ELSE '' END
    ) AS full_path_zh,
    CONCAT(
        COALESCE(c1.name_en, ''),
        CASE WHEN c2.name_en != '' THEN CONCAT('/', c2.name_en) ELSE '' END,
        CASE WHEN c3.name_en != '' THEN CONCAT('/', c3.name_en) ELSE '' END
    ) AS full_path_en
FROM ozon_categories c1
LEFT JOIN ozon_categories c2 ON c2.parent_id = c1.category_id AND c2.level = 2
LEFT JOIN ozon_categories c3 ON c3.parent_id = c2.category_id AND c3.level = 3
WHERE c1.level = 1;
