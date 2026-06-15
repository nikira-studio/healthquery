CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingest_batches (
  batch_id TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'companion',
  received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'received',
  payload_json TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS metric_intervals (
  record_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT,
  numeric_value REAL,
  text_value TEXT,
  unit TEXT,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metric_intervals_type_start
  ON metric_intervals (metric_type, start_time);

CREATE INDEX IF NOT EXISTS idx_metric_intervals_batch_id
  ON metric_intervals (batch_id);

CREATE TABLE IF NOT EXISTS sleep_sessions (
  session_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  duration_minutes REAL,
  efficiency_pct REAL,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sleep_sessions_start_time
  ON sleep_sessions (start_time);

CREATE TABLE IF NOT EXISTS sleep_stages (
  stage_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
  session_key TEXT NOT NULL REFERENCES sleep_sessions(session_key) ON DELETE CASCADE,
  source TEXT NOT NULL,
  stage_type TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  duration_seconds INTEGER,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sleep_stages_session_key
  ON sleep_stages (session_key);

CREATE TABLE IF NOT EXISTS workouts (
  workout_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  activity_type TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  duration_minutes REAL,
  calories REAL,
  avg_hr REAL,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workouts_start_time
  ON workouts (start_time);

CREATE TABLE IF NOT EXISTS daily_summaries (
  summary_date TEXT PRIMARY KEY,
  steps INTEGER NOT NULL DEFAULT 0,
  active_minutes INTEGER NOT NULL DEFAULT 0,
  sleep_minutes INTEGER NOT NULL DEFAULT 0,
  workouts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reports (
  report_id TEXT PRIMARY KEY,
  report_type TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS timeline_events (
  event_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  event_time TEXT NOT NULL,
  title TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
