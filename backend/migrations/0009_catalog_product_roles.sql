ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS role VARCHAR(80);

UPDATE catalog_products
SET role = CASE
    WHEN lower(category) = 'table' AND position('coffee table' in lower(title)) > 0 THEN 'coffee_table'
    WHEN lower(category) = 'table' AND position('nesting table' in lower(title)) > 0 THEN 'coffee_table'
    WHEN lower(category) = 'table' AND position('dining table' in lower(title)) > 0 THEN 'dining_table'
    WHEN lower(category) = 'table' AND (
        position('storage combination' in lower(title)) > 0
        OR position('chest of' in lower(title)) > 0
        OR position('shelving unit' in lower(title)) > 0
    ) THEN 'storage'
    WHEN lower(category) = 'table' THEN 'side_table'
    WHEN lower(category) = 'decor' AND position('shelving unit' in lower(title)) > 0 THEN 'storage'
    ELSE lower(category)
END
WHERE role IS NULL OR role = '';

ALTER TABLE catalog_products ALTER COLUMN role SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_catalog_products_role ON catalog_products(role);
