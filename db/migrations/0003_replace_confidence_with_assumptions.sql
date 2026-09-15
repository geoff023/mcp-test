-- Slice 4. Replaces the numeric confidence (0.0-1.0) with a free-text
-- assumptions field. A percentage implied a precision the model doesn't
-- actually have; the agent now states plainly what it assumed (an unclear
-- acceptance criterion, an unknown dependency, etc.), or the literal
-- string 'NA' when it made none - something a PM can actually evaluate,
-- not a number to blindly trust or distrust. See CLAUDE.md's slice-4 note.
--
-- SQLite's ALTER TABLE can't change a column's type or drop NOT NULL in
-- place, so this rebuilds the table via the standard rename/recreate/copy
-- pattern rather than a plain ALTER.
ALTER TABLE staged_changes RENAME TO staged_changes_old;

CREATE TABLE staged_changes (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  issue_key     TEXT NOT NULL,
  field         TEXT NOT NULL,         -- 'story_points'
  old_value     TEXT,
  new_value     TEXT NOT NULL,
  reasoning     TEXT NOT NULL,         -- why the agent proposed this
  assumptions   TEXT NOT NULL DEFAULT 'NA',  -- what it assumed, or 'NA'
  edited_value  TEXT,
  acknowledged  INTEGER NOT NULL DEFAULT 0,
  applied_at    TEXT
);

INSERT INTO staged_changes
  (id, run_id, issue_key, field, old_value, new_value, reasoning, assumptions, edited_value, acknowledged, applied_at)
SELECT
  id, run_id, issue_key, field, old_value, new_value, reasoning, 'NA', edited_value, acknowledged, applied_at
FROM staged_changes_old;

DROP TABLE staged_changes_old;

CREATE INDEX IF NOT EXISTS idx_staged_changes_run_id ON staged_changes(run_id);
