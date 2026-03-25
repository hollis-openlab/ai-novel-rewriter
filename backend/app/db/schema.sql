PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS novels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_format TEXT NOT NULL CHECK (file_format IN ('txt', 'epub')),
    file_size INTEGER NOT NULL,
    total_chars INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    config_override_json TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    novel_id TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    source_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    auto_execute INTEGER NOT NULL DEFAULT 0,
    artifact_root TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    paragraph_count INTEGER NOT NULL,
    UNIQUE(task_id, chapter_index)
);

CREATE TABLE IF NOT EXISTS stage_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN ('import', 'split', 'analyze', 'mark', 'rewrite', 'assemble')),
    run_seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'paused', 'stale')),
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    run_idempotency_key TEXT,
    config_snapshot_json TEXT,
    warnings_count INTEGER NOT NULL DEFAULT 0,
    chapters_total INTEGER NOT NULL DEFAULT 0,
    chapters_done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(task_id, stage, run_seq)
);

CREATE TABLE IF NOT EXISTS chapter_states (
    id TEXT PRIMARY KEY,
    stage_run_id TEXT NOT NULL REFERENCES stage_runs(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(stage_run_id, chapter_index)
);

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL CHECK (provider_type IN ('openai', 'openai_compatible')),
    credential_fingerprint TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model_name TEXT NOT NULL,
    temperature REAL NOT NULL DEFAULT 0.7,
    max_tokens INTEGER NOT NULL DEFAULT 4000,
    top_p REAL,
    presence_penalty REAL,
    frequency_penalty REAL,
    model_list_cache_json TEXT,
    model_list_fetched_at TEXT,
    rpm_limit INTEGER NOT NULL DEFAULT 60,
    tpm_limit INTEGER NOT NULL DEFAULT 100000,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(provider_type, base_url, credential_fingerprint)
);

CREATE TABLE IF NOT EXISTS configs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'global' CHECK (scope IN ('global', 'novel')),
    novel_id TEXT REFERENCES novels(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_one_active_per_novel
    ON tasks(novel_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_chapters_task_index
    ON chapters(task_id, chapter_index);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stage_runs_singleflight
    ON stage_runs(task_id, stage)
    WHERE status = 'running';

CREATE UNIQUE INDEX IF NOT EXISTS idx_stage_runs_idempotency
    ON stage_runs(task_id, stage, run_idempotency_key)
    WHERE run_idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_stage_runs_latest
    ON stage_runs(task_id, stage, run_seq DESC);

