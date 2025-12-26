-- PartSelect Chat Agent Database Schema
-- Run this in Supabase SQL Editor

-- Enable pgvector extension for embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- SQL TABLES (Exact Lookups)
-- =============================================================================

-- 1. Parts table - Product catalog with semantic search embedding
CREATE TABLE IF NOT EXISTS parts (
    ps_number TEXT PRIMARY KEY,
    part_name TEXT,
    part_type TEXT,
    manufacturer_part_number TEXT,
    part_manufacturer TEXT,
    part_price DECIMAL(10, 2),
    part_description TEXT,
    install_difficulty TEXT,
    install_time TEXT,
    install_video_url TEXT,
    part_url TEXT,
    average_rating DECIMAL(3, 2),
    num_reviews INTEGER,
    appliance_type TEXT NOT NULL,
    brand TEXT,
    manufactured_for TEXT,
    availability TEXT,
    replaces_parts TEXT,
    embedding vector(384),  -- Semantic embedding of name + type + description
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_parts_appliance_type ON parts(appliance_type);
CREATE INDEX IF NOT EXISTS idx_parts_part_type ON parts(part_type);
CREATE INDEX IF NOT EXISTS idx_parts_brand ON parts(brand);
-- Vector similarity search index for parts
CREATE INDEX IF NOT EXISTS idx_parts_embedding ON parts
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 2. Model Compatibility table - Part-model relationships
CREATE TABLE IF NOT EXISTS model_compatibility (
    part_id TEXT NOT NULL REFERENCES parts(ps_number) ON DELETE CASCADE,
    model_number TEXT NOT NULL,
    brand TEXT,
    description TEXT,
    PRIMARY KEY (part_id, model_number)
);

-- Index for model lookups
CREATE INDEX IF NOT EXISTS idx_model_compat_model ON model_compatibility(model_number);

-- 3. Repair Symptoms table - Common problems
CREATE TABLE IF NOT EXISTS repair_symptoms (
    id SERIAL PRIMARY KEY,
    appliance_type TEXT NOT NULL,
    symptom TEXT NOT NULL,
    symptom_description TEXT,
    percentage DECIMAL(5, 2),
    video_url TEXT,
    parts TEXT,
    symptom_url TEXT,
    difficulty TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (appliance_type, symptom)
);

-- Index for symptom lookups
CREATE INDEX IF NOT EXISTS idx_symptoms_appliance ON repair_symptoms(appliance_type);

-- 4. Repair Instructions table - Diagnostic steps
CREATE TABLE IF NOT EXISTS repair_instructions (
    id SERIAL PRIMARY KEY,
    appliance_type TEXT NOT NULL,
    symptom TEXT NOT NULL,
    part_type TEXT NOT NULL,
    instructions TEXT,
    part_category_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (appliance_type, symptom, part_type)
);

-- Index for instruction lookups
CREATE INDEX IF NOT EXISTS idx_instructions_symptom ON repair_instructions(appliance_type, symptom);

-- =============================================================================
-- VECTOR TABLES (Semantic Search)
-- =============================================================================

-- 5. Q&A Embeddings table
CREATE TABLE IF NOT EXISTS qna_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    asker TEXT,
    date TEXT,
    model_number TEXT,
    helpful_count INTEGER DEFAULT 0,
    embedding_text TEXT,
    embedding vector(384),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (ps_number, question_id)
);

-- Vector similarity search index
CREATE INDEX IF NOT EXISTS idx_qna_embedding ON qna_embeddings
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 6. Repair Stories Embeddings table
CREATE TABLE IF NOT EXISTS repair_stories_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number) ON DELETE CASCADE,
    story_id TEXT NOT NULL,
    title TEXT,
    instruction TEXT,
    author TEXT,
    difficulty TEXT,
    repair_time TEXT,
    helpful_count INTEGER DEFAULT 0,
    vote_count INTEGER DEFAULT 0,
    embedding_text TEXT,
    embedding vector(384),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (ps_number, story_id)
);

-- Vector similarity search index
CREATE INDEX IF NOT EXISTS idx_stories_embedding ON repair_stories_embeddings
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 7. Reviews Embeddings table
CREATE TABLE IF NOT EXISTS reviews_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number) ON DELETE CASCADE,
    review_id TEXT NOT NULL,
    rating INTEGER,
    title TEXT,
    content TEXT,
    author TEXT,
    date TEXT,
    verified_purchase BOOLEAN DEFAULT FALSE,
    embedding_text TEXT,
    embedding vector(384),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (ps_number, review_id)
);

-- Vector similarity search index
CREATE INDEX IF NOT EXISTS idx_reviews_embedding ON reviews_embeddings
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- =============================================================================
-- HELPER FUNCTIONS
-- =============================================================================

-- Function to search Q&A by semantic similarity
-- Optionally filter by ps_number to get Q&A for a specific part
CREATE OR REPLACE FUNCTION search_qna(
    query_embedding vector(384),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_ps_number TEXT DEFAULT NULL
)
RETURNS TABLE (
    id INT,
    ps_number TEXT,
    question TEXT,
    answer TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        qna_embeddings.id,
        qna_embeddings.ps_number,
        qna_embeddings.question,
        qna_embeddings.answer,
        1 - (qna_embeddings.embedding <=> query_embedding) AS similarity
    FROM qna_embeddings
    WHERE 1 - (qna_embeddings.embedding <=> query_embedding) > match_threshold
      AND (filter_ps_number IS NULL OR qna_embeddings.ps_number = filter_ps_number)
    ORDER BY qna_embeddings.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Function to search repair stories by semantic similarity
-- Optionally filter by ps_number to get stories for a specific part
CREATE OR REPLACE FUNCTION search_repair_stories(
    query_embedding vector(384),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_ps_number TEXT DEFAULT NULL
)
RETURNS TABLE (
    id INT,
    ps_number TEXT,
    title TEXT,
    instruction TEXT,
    difficulty TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        repair_stories_embeddings.id,
        repair_stories_embeddings.ps_number,
        repair_stories_embeddings.title,
        repair_stories_embeddings.instruction,
        repair_stories_embeddings.difficulty,
        1 - (repair_stories_embeddings.embedding <=> query_embedding) AS similarity
    FROM repair_stories_embeddings
    WHERE 1 - (repair_stories_embeddings.embedding <=> query_embedding) > match_threshold
      AND (filter_ps_number IS NULL OR repair_stories_embeddings.ps_number = filter_ps_number)
    ORDER BY repair_stories_embeddings.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Function to search parts by semantic similarity
-- Use for natural language queries like "refrigerator bins" -> matches "Drawer or Glides"
CREATE OR REPLACE FUNCTION search_parts_semantic(
    query_embedding vector(384),
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 10,
    filter_appliance_type TEXT DEFAULT NULL
)
RETURNS TABLE (
    ps_number TEXT,
    part_name TEXT,
    part_type TEXT,
    part_price DECIMAL(10, 2),
    average_rating DECIMAL(3, 2),
    num_reviews INTEGER,
    availability TEXT,
    brand TEXT,
    part_url TEXT,
    manufacturer_part_number TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        parts.ps_number,
        parts.part_name,
        parts.part_type,
        parts.part_price,
        parts.average_rating,
        parts.num_reviews,
        parts.availability,
        parts.brand,
        parts.part_url,
        parts.manufacturer_part_number,
        1 - (parts.embedding <=> query_embedding) AS similarity
    FROM parts
    WHERE parts.embedding IS NOT NULL
      AND 1 - (parts.embedding <=> query_embedding) > match_threshold
      AND (filter_appliance_type IS NULL OR parts.appliance_type = filter_appliance_type)
    ORDER BY parts.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Function to search reviews by semantic similarity
-- Use for questions like "is this part easy to install?" or "any quality issues?"
CREATE OR REPLACE FUNCTION search_reviews(
    query_embedding vector(384),
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5,
    filter_ps_number TEXT DEFAULT NULL
)
RETURNS TABLE (
    id INT,
    ps_number TEXT,
    rating INTEGER,
    title TEXT,
    content TEXT,
    author TEXT,
    verified_purchase BOOLEAN,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        reviews_embeddings.id,
        reviews_embeddings.ps_number,
        reviews_embeddings.rating,
        reviews_embeddings.title,
        reviews_embeddings.content,
        reviews_embeddings.author,
        reviews_embeddings.verified_purchase,
        1 - (reviews_embeddings.embedding <=> query_embedding) AS similarity
    FROM reviews_embeddings
    WHERE 1 - (reviews_embeddings.embedding <=> query_embedding) > match_threshold
      AND (filter_ps_number IS NULL OR reviews_embeddings.ps_number = filter_ps_number)
    ORDER BY reviews_embeddings.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
