CREATE TABLE IF NOT EXISTS metric_points (
  record_key TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  numeric_value REAL,
  text_value TEXT,
  unit TEXT,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metric_points_type_time
  ON metric_points (metric_type, recorded_at);

CREATE INDEX IF NOT EXISTS idx_metric_points_batch_id
  ON metric_points (batch_id);
