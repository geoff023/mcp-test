-- Slice 1 schema. See docs/slice-1.md for the ER description.
-- audit_log is append-only: nothing in the codebase may UPDATE or DELETE a row in it.

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,      -- uuid
  level         TEXT NOT NULL,         -- 'L1' | 'L2' | 'L3' | 'L4'
  task_type     TEXT NOT NULL,         -- 'reestimate'
  agent         TEXT NOT NULL,         -- 'planning'
  scope         TEXT NOT NULL,         -- JQL or issue key list
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL,         -- 'running' | 'awaiting_review' | 'applied' | 'rejected'
  comment       TEXT                   -- set when sent back at review time
);

CREATE TABLE IF NOT EXISTS staged_changes (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  issue_key     TEXT NOT NULL,
  field         TEXT NOT NULL,         -- 'story_points'
  old_value     TEXT,
  new_value     TEXT NOT NULL,
  reasoning     TEXT NOT NULL,         -- why the agent proposed this
  confidence    REAL NOT NULL,         -- 0.0 - 1.0
  acknowledged  INTEGER NOT NULL DEFAULT 0,
  applied_at    TEXT
);

CREATE TABLE IF NOT EXISTS approval_tokens (
  token         TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  issued_at     TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  consumed_at   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  run_id        TEXT,
  actor         TEXT NOT NULL,         -- 'agent:planning' | 'human:selena' | 'gateway'
  level         TEXT,
  action        TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  detail        TEXT
);

CREATE INDEX IF NOT EXISTS idx_staged_changes_run_id ON staged_changes(run_id);
CREATE INDEX IF NOT EXISTS idx_approval_tokens_run_id ON approval_tokens(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_run_id ON audit_log(run_id);
