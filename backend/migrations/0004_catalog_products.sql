CREATE TABLE IF NOT EXISTS catalog_products (
    product_id VARCHAR(80) PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    category VARCHAR(80) NOT NULL,
    theme VARCHAR(120) NOT NULL,
    price_inr NUMERIC(12, 2) NOT NULL CHECK (price_inr > 0),
    dimensions_m JSON NOT NULL,
    primary_color VARCHAR(160) NOT NULL,
    primary_material TEXT NOT NULL,
    prompt_phrase TEXT NOT NULL,
    image_s3_key VARCHAR(1024) NOT NULL UNIQUE,
    buy_url TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_catalog_products_category ON catalog_products(category);
CREATE INDEX IF NOT EXISTS ix_catalog_products_theme ON catalog_products(theme);
