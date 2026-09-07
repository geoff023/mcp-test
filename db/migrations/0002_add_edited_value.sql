-- Slice 2. At L2 a human can edit the agent's proposed value before
-- approving. new_value stays exactly what the agent proposed - this column
-- holds what the human actually chose to apply, so provenance keeps both.
ALTER TABLE staged_changes ADD COLUMN edited_value TEXT;
