ALTER TABLE catalog_products
    ADD COLUMN IF NOT EXISTS image_status VARCHAR(20) NOT NULL DEFAULT 'pending';

UPDATE catalog_products
SET image_status = CASE
    WHEN reference_image_s3_key IS NOT NULL THEN 'ready'
    WHEN kind = 'surface' THEN 'not_required'
    ELSE 'pending'
END
WHERE image_status = 'pending';
