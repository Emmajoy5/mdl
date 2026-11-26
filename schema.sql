-- =====================================================================
--  Microbial Digital Library -- Catalog Schema
--  Authoritative schema for the freezer library + validation panel.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- Organisms: one row per species (or strain, when a strain is tracked).
CREATE TABLE IF NOT EXISTS organisms (
    organism_id        INTEGER PRIMARY KEY,
    collection_id      TEXT    NOT NULL UNIQUE,          -- e.g. MDL-SACCER-001
    genus              TEXT    NOT NULL,
    species            TEXT    NOT NULL,                 -- specific epithet, or 'spp.'
    strain             TEXT,                             -- strain designation if known
    full_name          TEXT    NOT NULL,                 -- canonical display name
    ncbi_taxonomy_id   INTEGER,
    target_status      TEXT    NOT NULL DEFAULT 'initial'
                       CHECK (target_status IN ('initial','candidate','curated','retired')),
    collection_type    TEXT    NOT NULL DEFAULT 'library'
                       CHECK (collection_type IN ('library','validation','reference','other')),
    notes              TEXT,

    -- Extended lab metadata
    gram_stain         TEXT,
    media              TEXT,
    sample_source      TEXT,
    oxygen_requirement TEXT,
    optimal_temp_c     TEXT,
    morphology_notes   TEXT,
    storage_location   TEXT,
    storage_format     TEXT,
    storage_temp_c     TEXT,
    date_stored        TEXT,
    last_verified_date TEXT,
    shape_config       TEXT,                             -- JSON override for 3D shape params
    cell_shape         TEXT,                             -- chosen on the strain page; NULL = automatic
    cell_arrangement   TEXT,

    -- Attribution
    added_by           TEXT,

    -- Viability / revival tracking
    viability_category  TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (viability_category IN ('periodic','stable','longTerm','unknown')),
    revival_interval_days INTEGER,
    last_revival_date     TEXT,
    growth_success         TEXT CHECK (growth_success IN ('pass','fail')),
    contamination_notes    TEXT,

    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Accessions: culture-collection cross-references (NCYC/DSM/ATCC/CBS, yeast labs...).
CREATE TABLE IF NOT EXISTS accessions (
    accession_id       INTEGER PRIMARY KEY,
    organism_id        INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    culture_collection TEXT    NOT NULL,                 -- e.g. NCYC, DSM, ATCC, CBS, Wyeast
    accession_number   TEXT    NOT NULL,
    date_acquired      TEXT,
    UNIQUE (culture_collection, accession_number)
);

-- Plate images: standardized capture metadata (feed image-to-3D pipeline).
CREATE TABLE IF NOT EXISTS plate_images (
    image_id                INTEGER PRIMARY KEY,
    organism_id             INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    file_path               TEXT,
    capture_protocol_version TEXT,                      -- which SOP version was used
    lighting                TEXT,                       -- e.g. 'top/transmitted/diffuse'
    angle                   TEXT,                       -- e.g. 'nadir (top-down)'
    scale_bar_present       INTEGER NOT NULL DEFAULT 0, -- 0/1
    resolution              TEXT,
    background              TEXT,
    license                 TEXT,
    captured_by             TEXT,
    captured_date           TEXT,

    -- Attribution for the upload itself (may differ from who captured the photo)
    uploaded_by             TEXT,
    upload_timestamp        TEXT,

    -- Classical computer-vision morphology + colony counting
    cv_morphology_json      TEXT,                       -- classical-CV morphology (coverage/elevation/margin)
    bg_marker_x             REAL,                       -- manual background-marker override (fraction 0..1)
    bg_marker_y             REAL,
    colony_count            INTEGER,
    colony_coordinates      TEXT,                       -- JSON list of {x_frac,y_frac,diameter_mm}
    segmentation_confidence REAL,

    qc_status               TEXT,
    qc_notes                TEXT,
    detected_artifacts      TEXT,
    ai_analyzed_at          TEXT,

    UNIQUE (file_path)
);

-- 3D models: one row per model file (a species can have several).
CREATE TABLE IF NOT EXISTS models (
    model_id        INTEGER PRIMARY KEY,
    organism_id     INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    model_type      TEXT    NOT NULL,                  -- colony / cell / stylized / photo_relief
    source          TEXT    NOT NULL,                  -- procedural / photo / meshy / tripo / manual
    file_format     TEXT    NOT NULL,                  -- STL / GLB / OBJ / PLY
    file_path       TEXT,
    scale_factor    REAL,                              -- linear scale applied (1.0 = native)
    level_of_detail TEXT,                              -- low / medium / high
    is_print_ready  INTEGER NOT NULL DEFAULT 0,        -- 0/1
    is_watertight   INTEGER,                           -- 0/1 (required for printing)
    fidelity_score  REAL,                              -- QA 0..1 vs. reference
    qa_status       TEXT    NOT NULL DEFAULT 'draft'
                    CHECK (qa_status IN ('draft','review','approved','rejected')),
    version         INTEGER NOT NULL DEFAULT 1,
    created_date    TEXT,
    notes           TEXT,
    UNIQUE (file_path)
);

-- Brewing relevance: structured, strain-specific attributes.
CREATE TABLE IF NOT EXISTS brewing_relevance (
    relevance_id        INTEGER PRIMARY KEY,
    organism_id         INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    role                TEXT,                          -- canonical: fermentation/souring/spoilage/conditioning/flavor/other
    attenuation_min     REAL,                          -- % apparent attenuation
    attenuation_max     REAL,
    temp_range_c        TEXT,                          -- e.g. '18-24'
    flavor_compounds    TEXT,                          -- comma-separated
    off_flavor_potential TEXT,
    spoilage_risk       TEXT CHECK (spoilage_risk IN ('none','low','medium','high')),
    application_notes   TEXT
);

-- Tags: free-form, extensible facets (e.g. 'ale','lager','wild','sour','diacetyl').
CREATE TABLE IF NOT EXISTS tags (
    tag_id      INTEGER PRIMARY KEY,
    organism_id INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    tag         TEXT    NOT NULL,
    UNIQUE (organism_id, tag)
);

-- Revival log: full history of viability checks for periodic strains.
CREATE TABLE IF NOT EXISTS revival_log (
    log_id              INTEGER PRIMARY KEY,
    organism_id         INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    revival_date        TEXT    NOT NULL,
    performed_by        TEXT,
    growth_success      TEXT CHECK (growth_success IN ('pass','fail')),
    contamination_notes TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Gram stain images: dated history of Gram stain results, each optionally
-- with its own micrograph (a strain can be re-stained later; organisms.gram_stain
-- always holds the most recent result for quick display/filtering).
CREATE TABLE IF NOT EXISTS gram_stain_images (
    image_id         INTEGER PRIMARY KEY,
    organism_id      INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    file_path        TEXT,
    result           TEXT CHECK (result IN ('positive','negative','variable','not_done')),
    magnification    TEXT,
    stained_by       TEXT,
    stain_date       TEXT,
    notes            TEXT,
    cell_shape       TEXT,              -- what the cells looked like (coccus, rod, ...)
    cell_arrangement TEXT,              -- single, pair, chain, cluster, ...
    uploaded_by      TEXT,
    upload_timestamp TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for the searchable dashboard.
CREATE INDEX IF NOT EXISTS idx_org_collection  ON organisms(collection_id);
CREATE INDEX IF NOT EXISTS idx_org_genus       ON organisms(genus);
CREATE INDEX IF NOT EXISTS idx_org_type        ON organisms(collection_type);
CREATE INDEX IF NOT EXISTS idx_models_org      ON models(organism_id);
CREATE INDEX IF NOT EXISTS idx_relevance_org   ON brewing_relevance(organism_id);
CREATE INDEX IF NOT EXISTS idx_images_org      ON plate_images(organism_id);
CREATE INDEX IF NOT EXISTS idx_tags_org        ON tags(organism_id);
CREATE INDEX IF NOT EXISTS idx_revival_org     ON revival_log(organism_id);
CREATE INDEX IF NOT EXISTS idx_gram_org        ON gram_stain_images(organism_id);
