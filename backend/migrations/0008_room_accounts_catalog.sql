CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(320) NOT NULL UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_projects_user_id ON projects(user_id);

CREATE TABLE IF NOT EXISTS rooms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type VARCHAR(80) NOT NULL,
    name VARCHAR(160) NOT NULL,
    budget NUMERIC(12,2),
    status VARCHAR(40) NOT NULL DEFAULT 'DRAFT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_rooms_project_id ON rooms(project_id);
CREATE INDEX IF NOT EXISTS ix_rooms_type ON rooms(type);

CREATE TABLE IF NOT EXISTS room_photos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    url VARCHAR(1024) NOT NULL,
    angle_label VARCHAR(80),
    "order" INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_room_photos_room_id ON room_photos(room_id);

CREATE TABLE IF NOT EXISTS designs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES designs(id) ON DELETE SET NULL,
    style VARCHAR(120) NOT NULL,
    palette VARCHAR(120) NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    image_url VARCHAR(1024),
    status VARCHAR(40) NOT NULL DEFAULT 'QUEUED',
    starred BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_designs_room_id ON designs(room_id);
CREATE INDEX IF NOT EXISTS ix_designs_parent_id ON designs(parent_id);
CREATE INDEX IF NOT EXISTS ix_designs_status ON designs(status);

CREATE TABLE IF NOT EXISTS design_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    design_id UUID NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
    catalog_item_id VARCHAR(80) NOT NULL REFERENCES catalog_products(product_id),
    x NUMERIC(7,5) NOT NULL,
    y NUMERIC(7,5) NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0)
);
CREATE INDEX IF NOT EXISTS ix_design_items_design_id ON design_items(design_id);
CREATE INDEX IF NOT EXISTS ix_design_items_catalog_item_id ON design_items(catalog_item_id);

CREATE TABLE IF NOT EXISTS usage (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    generations_used INTEGER NOT NULL DEFAULT 0,
    jobs_3d_used INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS room_id UUID REFERENCES rooms(id) ON DELETE CASCADE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS design_id UUID REFERENCES designs(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_jobs_room_id ON jobs(room_id);
CREATE INDEX IF NOT EXISTS ix_jobs_design_id ON jobs(design_id);

ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS room_types JSON NOT NULL DEFAULT '[]'::json;

INSERT INTO rooms(project_id, type, name, status)
SELECT p.id, COALESCE(NULLIF(p.room_type, ''), 'living_room'), p.name, 'DRAFT'
FROM projects p
WHERE NOT EXISTS (SELECT 1 FROM rooms r WHERE r.project_id = p.id);

INSERT INTO room_photos(room_id, url, angle_label, "order")
SELECT r.id, photo.url, NULL, (photo.ordinality - 1)::INTEGER
FROM projects p
JOIN rooms r ON r.project_id = p.id
CROSS JOIN LATERAL json_array_elements_text(COALESCE(p.photo_keys, '[]'::json)) WITH ORDINALITY AS photo(url, ordinality)
WHERE NOT EXISTS (SELECT 1 FROM room_photos rp WHERE rp.room_id = r.id);
