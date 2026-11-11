ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS kind VARCHAR(12) NOT NULL DEFAULT 'object';
ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS selectable BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS source_image_url TEXT;
ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS reference_image_s3_key VARCHAR(1024);

UPDATE catalog_products
SET kind = CASE WHEN role IN ('paint', 'flooring') THEN 'surface' ELSE 'object' END,
    source_image_url = COALESCE(source_image_url, NULLIF(image_url, '')),
    selectable = CASE
        WHEN role IN ('paint', 'flooring') THEN TRUE
        WHEN NULLIF(image_url, '') IS NULL THEN FALSE
        ELSE TRUE
    END;

ALTER TABLE designs ADD COLUMN IF NOT EXISTS structure_map_url VARCHAR(1024);
ALTER TABLE designs ADD COLUMN IF NOT EXISTS seed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE designs ADD COLUMN IF NOT EXISTS image_stale BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE designs ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE design_items ALTER COLUMN x DROP NOT NULL;
ALTER TABLE design_items ALTER COLUMN y DROP NOT NULL;
ALTER TABLE design_items ADD COLUMN IF NOT EXISTS role VARCHAR(80) NOT NULL DEFAULT 'decor';
ALTER TABLE design_items ADD COLUMN IF NOT EXISTS visible BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE design_items di
SET role = cp.role
FROM catalog_products cp
WHERE cp.product_id = di.catalog_item_id AND di.role = 'decor';
