ALTER TABLE designs
    ADD COLUMN IF NOT EXISTS render_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
