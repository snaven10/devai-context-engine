package session

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS sessions (
	id TEXT PRIMARY KEY,
	repo_path TEXT NOT NULL,
	branch TEXT NOT NULL DEFAULT '',
	started_at TEXT NOT NULL,
	ended_at TEXT,
	source TEXT NOT NULL DEFAULT 'cli',
	active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS session_events (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	session_id TEXT NOT NULL,
	timestamp TEXT NOT NULL,
	event_type TEXT NOT NULL,
	tool_name TEXT,
	input_summary TEXT,
	output_summary TEXT,
	duration_ms INTEGER,
	branch TEXT,
	metadata TEXT,
	FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON session_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON session_events(timestamp);

CREATE TABLE IF NOT EXISTS memories (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	repo_path TEXT NOT NULL DEFAULT '',
	branch TEXT NOT NULL DEFAULT '',
	commit_sha TEXT NOT NULL DEFAULT '',
	type TEXT NOT NULL DEFAULT 'note',
	scope TEXT NOT NULL DEFAULT 'shared',
	author TEXT NOT NULL DEFAULT '',
	text TEXT NOT NULL,
	tags TEXT NOT NULL DEFAULT '',
	content_hash TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL,
	archived INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_path, branch);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
`

// Store manages sessions and events in SQLite.
type Store struct {
	db *sql.DB
}

// Session represents an active or past session.
type Session struct {
	ID        string
	RepoPath  string
	Branch    string
	StartedAt time.Time
	EndedAt   *time.Time
	Source    string
	Active    bool
}

// Event represents a session event.
type Event struct {
	ID            int64
	SessionID     string
	Timestamp     time.Time
	EventType     string
	ToolName      string
	InputSummary  string
	OutputSummary string
	DurationMs    int64
	Branch        string
	Metadata      map[string]interface{}
}

// Memory represents a stored memory entry.
type Memory struct {
	ID          int64
	RepoPath    string
	Branch      string
	CommitSHA   string
	Type        string
	Scope       string
	Author      string
	Text        string
	Tags        string
	ContentHash string
	CreatedAt   time.Time
	UpdatedAt   time.Time
	Archived    bool
}

// NewStore creates a new session store.
func NewStore(dbPath string) (*Store, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("opening database: %w", err)
	}

	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return nil, fmt.Errorf("setting WAL mode: %w", err)
	}

	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("creating schema: %w", err)
	}

	return &Store{db: db}, nil
}

// Close closes the database connection.
func (s *Store) Close() error {
	return s.db.Close()
}

// StartSession creates a new session.
func (s *Store) StartSession(repoPath, branch, source string) (*Session, error) {
	// End any active sessions first
	now := time.Now().UTC()
	s.db.Exec(
		"UPDATE sessions SET active = 0, ended_at = ? WHERE active = 1",
		now.Format(time.RFC3339),
	)

	id := fmt.Sprintf("%d", now.UnixNano())
	sess := &Session{
		ID:        id,
		RepoPath:  repoPath,
		Branch:    branch,
		StartedAt: now,
		Source:    source,
		Active:    true,
	}

	_, err := s.db.Exec(
		"INSERT INTO sessions (id, repo_path, branch, started_at, source, active) VALUES (?, ?, ?, ?, ?, 1)",
		sess.ID, sess.RepoPath, sess.Branch, now.Format(time.RFC3339), sess.Source,
	)
	if err != nil {
		return nil, fmt.Errorf("creating session: %w", err)
	}

	return sess, nil
}

// EndSession marks a session as ended.
func (s *Store) EndSession(sessionID string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := s.db.Exec(
		"UPDATE sessions SET active = 0, ended_at = ? WHERE id = ?",
		now, sessionID,
	)
	return err
}

// LogEvent records a session event.
func (s *Store) LogEvent(sessionID string, evt Event) error {
	metaJSON := "{}"
	if evt.Metadata != nil {
		data, _ := json.Marshal(evt.Metadata)
		metaJSON = string(data)
	}

	_, err := s.db.Exec(
		`INSERT INTO session_events
		 (session_id, timestamp, event_type, tool_name, input_summary, output_summary, duration_ms, branch, metadata)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		sessionID,
		evt.Timestamp.Format(time.RFC3339),
		evt.EventType,
		evt.ToolName,
		evt.InputSummary,
		evt.OutputSummary,
		evt.DurationMs,
		evt.Branch,
		metaJSON,
	)
	return err
}

// GetRecentEvents returns recent events across all sessions.
func (s *Store) GetRecentEvents(limit int) ([]Event, error) {
	rows, err := s.db.Query(
		`SELECT id, session_id, timestamp, event_type, tool_name,
		        COALESCE(input_summary, ''), COALESCE(output_summary, ''),
		        COALESCE(duration_ms, 0), COALESCE(branch, ''), COALESCE(metadata, '{}')
		 FROM session_events
		 ORDER BY timestamp DESC
		 LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []Event
	for rows.Next() {
		var evt Event
		var ts, metaStr string
		err := rows.Scan(
			&evt.ID, &evt.SessionID, &ts, &evt.EventType, &evt.ToolName,
			&evt.InputSummary, &evt.OutputSummary, &evt.DurationMs, &evt.Branch, &metaStr,
		)
		if err != nil {
			return nil, err
		}
		evt.Timestamp, _ = time.Parse(time.RFC3339, ts)
		json.Unmarshal([]byte(metaStr), &evt.Metadata)
		events = append(events, evt)
	}
	return events, rows.Err()
}

// SaveMemory stores a memory entry.
func (s *Store) SaveMemory(mem Memory) (int64, error) {
	now := time.Now().UTC().Format(time.RFC3339)
	result, err := s.db.Exec(
		`INSERT INTO memories
		 (repo_path, branch, commit_sha, type, scope, author, text, tags, content_hash, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		mem.RepoPath, mem.Branch, mem.CommitSHA, mem.Type, mem.Scope,
		mem.Author, mem.Text, mem.Tags, mem.ContentHash, now, now,
	)
	if err != nil {
		return 0, err
	}
	return result.LastInsertId()
}

// SearchMemories returns memories matching a text pattern.
func (s *Store) SearchMemories(query string, scope string, memType string, limit int) ([]Memory, error) {
	where := "archived = 0"
	args := []interface{}{}

	if scope != "" && scope != "all" {
		where += " AND scope = ?"
		args = append(args, scope)
	}
	if memType != "" {
		where += " AND type = ?"
		args = append(args, memType)
	}

	// Simple LIKE search for now — semantic search via vector store is the real path
	where += " AND text LIKE ?"
	args = append(args, "%"+query+"%")
	args = append(args, limit)

	rows, err := s.db.Query(
		fmt.Sprintf(
			`SELECT id, repo_path, branch, commit_sha, type, scope, author, text, tags,
			        content_hash, created_at, updated_at, archived
			 FROM memories WHERE %s ORDER BY created_at DESC LIMIT ?`, where),
		args...,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var memories []Memory
	for rows.Next() {
		var m Memory
		var createdAt, updatedAt string
		err := rows.Scan(
			&m.ID, &m.RepoPath, &m.Branch, &m.CommitSHA, &m.Type, &m.Scope,
			&m.Author, &m.Text, &m.Tags, &m.ContentHash, &createdAt, &updatedAt, &m.Archived,
		)
		if err != nil {
			return nil, err
		}
		m.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
		m.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)
		memories = append(memories, m)
	}
	return memories, rows.Err()
}
