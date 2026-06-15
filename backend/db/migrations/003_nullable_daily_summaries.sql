-- Make daily_summaries metric columns nullable.
-- 0 previously meant "no data captured"; NULL is the correct signal.
-- SQLite does not support ALTER COLUMN, so recreate the table.

ALTER TABLE daily_summaries RENAME TO daily_summaries_v2;

CREATE TABLE daily_summaries (
  summary_date    TEXT PRIMARY KEY,
  steps           INTEGER,
  active_minutes  INTEGER,
  sleep_minutes   INTEGER,
  workouts        INTEGER,
  updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO daily_summaries (summary_date, steps, active_minutes, sleep_minutes, workouts, updated_at)
SELECT
  summary_date,
  CASE WHEN steps         = 0 THEN NULL ELSE steps         END,
  CASE WHEN active_minutes = 0 THEN NULL ELSE active_minutes END,
  CASE WHEN sleep_minutes  = 0 THEN NULL ELSE sleep_minutes  END,
  CASE WHEN workouts       = 0 THEN NULL ELSE workouts       END,
  updated_at
FROM daily_summaries_v2;

DROP TABLE daily_summaries_v2;
