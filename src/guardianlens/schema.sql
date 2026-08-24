PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK(platform IN ('carousell','mudah')),
    marketplace_listing_id TEXT,
    source_url TEXT NOT NULL,
    source_url_hash TEXT NOT NULL,
    page_title TEXT,
    title TEXT,
    description TEXT,
    category TEXT,
    condition TEXT,
    price REAL,
    currency TEXT DEFAULT 'MYR',
    location_state TEXT,
    location_city TEXT,
    account_age_days INTEGER,
    seller_rating REAL,
    review_count INTEGER,
    active_listing_count INTEGER,
    language TEXT,
    status TEXT NOT NULL,
    metadata_signature TEXT,
    screenshot_path TEXT,
    staging_text_path TEXT,
    ai_extracted INTEGER NOT NULL DEFAULT 0,
    ai_provider TEXT,
    ai_model TEXT,
    ai_prompt_version TEXT,
    captured_at TEXT NOT NULL,
    processed_at TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    processing_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_listings_platform_marketplace_id
ON listings(platform, marketplace_listing_id)
WHERE marketplace_listing_id IS NOT NULL AND marketplace_listing_id <> '';
CREATE INDEX IF NOT EXISTS ix_listings_url_hash ON listings(source_url_hash);
CREATE INDEX IF NOT EXISTS ix_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS ix_listings_platform_category ON listings(platform, category);
CREATE INDEX IF NOT EXISTS ix_listings_metadata_signature ON listings(metadata_signature);

CREATE TABLE IF NOT EXISTS images (
    image_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    perceptual_hash TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    source_position INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_images_listing ON images(listing_id);
CREATE INDEX IF NOT EXISTS ix_images_sha256 ON images(sha256);
CREATE INDEX IF NOT EXISTS ix_images_phash ON images(perceptual_hash);

CREATE TABLE IF NOT EXISTS ai_extractions (
    extraction_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    structured_output TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS review_actions (
    review_action_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    previous_data TEXT,
    updated_data TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_seconds INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warnings (
    warning_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    warning_type TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_warnings_listing ON warnings(listing_id);

CREATE TABLE IF NOT EXISTS status_history (
    history_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS processing_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds INTEGER,
    duration_limit_minutes INTEGER,
    item_limit INTEGER,
    status TEXT NOT NULL,
    attempted INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    successful INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    ai_failures INTEGER NOT NULL DEFAULT 0,
    image_failures INTEGER NOT NULL DEFAULT 0,
    privacy_warnings INTEGER NOT NULL DEFAULT 0,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    other_failures INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processor_control (
    control_id INTEGER PRIMARY KEY CHECK(control_id = 1),
    command TEXT NOT NULL DEFAULT 'idle',
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO processor_control(control_id, command, updated_at) VALUES (1, 'idle', CURRENT_TIMESTAMP);
