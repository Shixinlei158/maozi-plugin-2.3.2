USE `ozon_selection`;

DELETE p
FROM `sku_products` p
LEFT JOIN `seed_skus` s
  ON s.`sku` = p.`sku`
 AND s.`status` = 'qualified'
WHERE s.`sku` IS NULL;

UPDATE `sku_universe` u
LEFT JOIN `seed_skus` s
  ON s.`sku` = u.`sku`
SET
  u.`is_formal_qualified` = CASE
    WHEN s.`status` = 'qualified' THEN 1
    WHEN s.`status` IS NOT NULL THEN 0
    ELSE u.`is_formal_qualified`
  END,
  u.`formal_rule_name` = CASE
    WHEN s.`status` IS NOT NULL THEN '0325 优质品'
    ELSE u.`formal_rule_name`
  END,
  u.`formal_rule_reason` = CASE
    WHEN s.`reason` IS NOT NULL THEN s.`reason`
    ELSE u.`formal_rule_reason`
  END;
