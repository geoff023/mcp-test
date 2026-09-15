-- Slice 4. Storage for the L1 (Advisor) conversational surface: a single
-- ongoing chat, read-only by construction because the agent driving it is
-- only ever started at L1 (search_issues/get_issue - no write tools; see
-- orchestrator/chat.py). Not tied to `runs` - a chat turn never opens a
-- run, since start_run isn't in L1's tool set at all.
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL REFERENCES chat_sessions(id),
  role          TEXT NOT NULL,         -- 'user' | 'agent'
  content       TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages(session_id);
