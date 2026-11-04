ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS s3_redesign_preview_key VARCHAR(1024);
