ALTER TABLE designs
    ADD COLUMN IF NOT EXISTS image_provider VARCHAR(40) DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS model_ids JSONB DEFAULT '{}'::jsonb;

UPDATE designs SET image_provider = 'unknown' WHERE image_provider IS NULL;
UPDATE designs SET model_ids = '{}'::jsonb WHERE model_ids IS NULL;

ALTER TABLE designs
    ALTER COLUMN image_provider SET DEFAULT 'unknown',
    ALTER COLUMN image_provider SET NOT NULL,
    ALTER COLUMN model_ids SET DEFAULT '{}'::jsonb,
    ALTER COLUMN model_ids SET NOT NULL;
