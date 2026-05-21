package api

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/snaven10/devai/internal/session"
	"github.com/snaven10/devai/internal/storage"
)

// Config holds API server configuration.
type Config struct {
	Addr     string
	APIToken string // simple token auth for now
	StateDir string // path that contains index.db (graph_edges table)
}

// Server is the shared mode HTTP API server.
//
// Memory endpoints (GET/POST /api/v1/memory/...) proxy to the Python ML service
// via mlclient — that's where the real memory store lives (with embeddings,
// topic-key upserts, and dedup). The legacy *session.Store is kept only for
// session/event tracking and is no longer touched by memory handlers.
type Server struct {
	config       Config
	sessionStore *session.Store
	ml           *mlclient.StdioClient
	webFS        fs.FS // optional embedded static assets served at /
	mux          *http.ServeMux
}

// New creates a new API server.
//
// ml must be a started StdioClient; the server reuses it for all memory ops.
// sessionStore may be nil if you only need memory + index endpoints.
// webFS is optional — when set, files inside are served at GET / (used by
// the visualizer page). Pass nil to disable static serving.
func New(cfg Config, sessionStore *session.Store, ml *mlclient.StdioClient, webFS fs.FS) *Server {
	s := &Server{
		config:       cfg,
		sessionStore: sessionStore,
		ml:           ml,
		webFS:        webFS,
		mux:          http.NewServeMux(),
	}
	s.routes()
	return s
}

// Start begins listening for requests.
func (s *Server) Start() error {
	srv := &http.Server{
		Addr:    s.config.Addr,
		Handler: s.mux,
		// Long writes allowed: backfill endpoints (re-embed + LanceDB search
		// across hundreds of memories) routinely run several minutes.
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 15 * time.Minute,
		IdleTimeout:  120 * time.Second,
	}
	log.Printf("API server listening on %s", s.config.Addr)
	return srv.ListenAndServe()
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /api/v1/health", s.handleHealth)
	s.mux.HandleFunc("GET /api/v1/index/{repo}", s.authMiddleware(s.handleGetIndex))
	s.mux.HandleFunc("POST /api/v1/index/{repo}/push", s.authMiddleware(s.handlePushIndex))
	s.mux.HandleFunc("POST /api/v1/index/{repo}/pull", s.authMiddleware(s.handlePullIndex))
	s.mux.HandleFunc("GET /api/v1/memory/{repo}", s.authMiddleware(s.handleGetMemories))
	s.mux.HandleFunc("POST /api/v1/memory/{repo}", s.authMiddleware(s.handlePostMemory))
	s.mux.HandleFunc("POST /api/v1/memory/{repo}/sync", s.authMiddleware(s.handleSyncMemory))
	s.mux.HandleFunc("GET /api/v1/memory/by-symbol", s.authMiddleware(s.handleMemoriesBySymbol))
	s.mux.HandleFunc("GET /api/v1/memory/by-file", s.authMiddleware(s.handleMemoriesByFile))
	s.mux.HandleFunc("GET /api/v1/memory/{id}/refs", s.authMiddleware(s.handleMemoryRefs))
	s.mux.HandleFunc("GET /api/v1/memory/{id}/graph", s.authMiddleware(s.handleMemoryGraph))
	s.mux.HandleFunc("GET /api/v1/memory/heatmap", s.authMiddleware(s.handleSymbolHeatmap))
	s.mux.HandleFunc("POST /api/v1/memory/backfill-refs", s.authMiddleware(s.handleBackfillRefs))
	s.mux.HandleFunc("POST /api/v1/memory/backfill-vector-links", s.authMiddleware(s.handleBackfillVectorLinks))
	s.mux.HandleFunc("GET /api/v1/graph/repos", s.authMiddleware(s.handleGraphRepos))
	s.mux.HandleFunc("GET /api/v1/graph/symbol", s.authMiddleware(s.handleGraphSymbol))
	s.mux.HandleFunc("GET /api/v1/graph/impact", s.authMiddleware(s.handleGraphImpact))
	s.mux.HandleFunc("POST /api/v1/graph/fts-rebuild", s.authMiddleware(s.handleGraphFTSRebuild))
	s.mux.HandleFunc("GET /api/v1/graph/search", s.authMiddleware(s.handleGraphSearch))
	s.mux.HandleFunc("GET /api/v1/graph/{repo}", s.authMiddleware(s.handleGetGraph))
	s.mux.HandleFunc("GET /api/v1/status", s.authMiddleware(s.handleStatus))

	if s.webFS != nil {
		s.mux.Handle("GET /", http.FileServer(http.FS(s.webFS)))
	}
}

func (s *Server) authMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.config.APIToken == "" {
			next(w, r)
			return
		}

		token := r.Header.Get("Authorization")
		if token == "" {
			token = r.URL.Query().Get("token")
		}

		token = strings.TrimPrefix(token, "Bearer ")
		if token != s.config.APIToken {
			writeJSON(w, http.StatusUnauthorized, map[string]string{
				"error": "invalid or missing API token",
			})
			return
		}
		next(w, r)
	}
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{
		"status":  "healthy",
		"version": "0.1.0",
	})
}

func (s *Server) handleGetIndex(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")
	branch := r.URL.Query().Get("branch")
	if branch == "" {
		branch = "main"
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"repo":   repo,
		"branch": branch,
		"note":   "Index metadata retrieval — pending shared store integration",
	})
}

func (s *Server) handlePushIndex(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")

	var body struct {
		Branch string `json:"branch"`
		Commit string `json:"commit"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}

	client, err := mlclient.NewStdioClient(
		mlclient.WithQuiet(),
		mlclient.WithEnv(storage.EnvVarsFromEnv()),
	)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": fmt.Sprintf("starting ML service: %v", err),
		})
		return
	}
	defer client.Close()

	result, err := client.PushIndex(repo, body.Branch)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": fmt.Sprintf("push-index failed: %v", err),
		})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handlePullIndex(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")

	var body struct {
		Branch     string `json:"branch"`
		FromCommit string `json:"from_commit"` // for incremental pull
	}
	json.NewDecoder(r.Body).Decode(&body)

	client, err := mlclient.NewStdioClient(
		mlclient.WithQuiet(),
		mlclient.WithEnv(storage.EnvVarsFromEnv()),
	)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": fmt.Sprintf("starting ML service: %v", err),
		})
		return
	}
	defer client.Close()

	result, err := client.PullIndex(repo, body.Branch)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": fmt.Sprintf("pull-index failed: %v", err),
		})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

// handleGetMemories lists memories. Path {repo} is the project filter.
// Query params: scope, type, q (semantic query), limit.
// If `q` is empty, falls back to memory_context (most recent). Otherwise uses recall (semantic).
func (s *Server) handleGetMemories(w http.ResponseWriter, r *http.Request) {
	project := r.PathValue("repo")
	scope := r.URL.Query().Get("scope")
	memType := r.URL.Query().Get("type")
	query := r.URL.Query().Get("q")
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}

	var (
		result interface{}
		err    error
	)
	if query == "" {
		result, err = s.ml.MemoryContext(project, scope, limit)
	} else {
		result, err = s.ml.Recall(query, scope, memType, project, limit)
	}
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"project": project,
		"query":   query,
		"result":  result,
	})
}

// handlePostMemory saves a memory via the Python store (proper embeddings + dedup).
// Body is the same shape the Python `remember` JSON-RPC method expects:
// {title, content, type, scope, project, topic_key, tags, files, ...}
// Path {repo} overrides body.project for safety.
func (s *Server) handlePostMemory(w http.ResponseWriter, r *http.Request) {
	project := r.PathValue("repo")

	var body map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	body["project"] = project

	result, err := s.ml.Remember(body)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"project": project,
		"result":  result,
	})
}

// handleMemoriesBySymbol returns memories that reference a given full symbol id.
// Query params: symbol (required), repo, branch, limit.
func (s *Server) handleMemoriesBySymbol(w http.ResponseWriter, r *http.Request) {
	symbol := r.URL.Query().Get("symbol")
	if symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "symbol query param required"})
		return
	}
	repo := r.URL.Query().Get("repo")
	branch := r.URL.Query().Get("branch")
	limit := 20
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}
	result, err := s.ml.MemoriesBySymbol(symbol, repo, branch, limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleMemoriesByFile returns memories that reference a file path.
func (s *Server) handleMemoriesByFile(w http.ResponseWriter, r *http.Request) {
	file := r.URL.Query().Get("file")
	if file == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "file query param required"})
		return
	}
	limit := 20
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}
	result, err := s.ml.MemoriesByFile(file, limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleMemoryRefs returns the junction rows for a memory id — used by the UI
// to know which symbols/files a memory talks about.
func (s *Server) handleMemoryRefs(w http.ResponseWriter, r *http.Request) {
	idStr := r.PathValue("id")
	var id int
	if _, err := fmt.Sscanf(idStr, "%d", &id); err != nil || id <= 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "memory id must be a positive integer"})
		return
	}
	result, err := s.ml.MemoryRefs(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleMemoryGraph returns a Cytoscape-ready subgraph built from EVERY symbol
// the memory references. One round-trip from the UI's "click a memory" flow.
// Query params: branch (required), hide_external (1/0), hide_synthetic (1/0).
func (s *Server) handleMemoryGraph(w http.ResponseWriter, r *http.Request) {
	idStr := r.PathValue("id")
	var id int
	if _, err := fmt.Sscanf(idStr, "%d", &id); err != nil || id <= 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "memory id must be a positive integer"})
		return
	}
	branch := r.URL.Query().Get("branch")
	if branch == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "branch query param required"})
		return
	}
	hideExternal := r.URL.Query().Get("hide_external") == "1"
	hideSynthetic := r.URL.Query().Get("hide_synthetic") == "1"

	refsRaw, err := s.ml.MemoryRefs(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	// Extract the symbols from the refs payload (it's untyped interface{} from JSON-RPC)
	refsMap, _ := refsRaw.(map[string]interface{})
	rawRefs, _ := refsMap["refs"].([]interface{})
	symbols := make([]interface{}, 0, len(rawRefs))
	for _, r := range rawRefs {
		row, _ := r.(map[string]interface{})
		if row == nil {
			continue
		}
		sym, _ := row["symbol"].(string)
		if sym != "" {
			symbols = append(symbols, sym)
		}
	}
	if len(symbols) == 0 {
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"memory_id": id, "branch": branch,
			"nodes": []interface{}{}, "edges": []interface{}{},
			"note": "memory has no symbol-level refs (only file-level or none)",
		})
		return
	}

	db, err := s.openIndexDB()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer db.Close()

	placeholders := strings.Repeat("?,", len(symbols))
	placeholders = placeholders[:len(placeholders)-1]
	q := "SELECT source, target, kind, source_file, line, repo FROM graph_edges " +
		"WHERE branch = ? AND (source IN (" + placeholders + ") OR target IN (" + placeholders + ")) LIMIT 500"
	args := []interface{}{branch}
	args = append(args, symbols...)
	args = append(args, symbols...)

	rows, err := db.Query(q, args...)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()

	nodes, edges := buildCytoscape(rows, hideExternal, hideSynthetic)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"memory_id":      id,
		"branch":         branch,
		"target_symbols": symbols,
		"nodes":          nodes,
		"edges":          edges,
	})
}

// handleSymbolHeatmap returns {symbol: count} for nodes with linked memories.
func (s *Server) handleSymbolHeatmap(w http.ResponseWriter, r *http.Request) {
	result, err := s.ml.SymbolMemoryCounts(r.URL.Query().Get("repo"), r.URL.Query().Get("branch"))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleBackfillRefs re-extracts memory↔symbol references across all memories.
// Use after upgrading or after a fresh index. POST so it's not idempotently cached.
func (s *Server) handleBackfillRefs(w http.ResponseWriter, r *http.Request) {
	result, err := s.ml.BackfillSymbolRefs()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleBackfillVectorLinks bridges memories without explicit code mentions
// to code chunks via vector similarity (LanceDB).
// Query params:
//
//	top_k          (default 5)    — nearest code chunks per memory
//	only_unlinked  (default true) — false = re-link everything
func (s *Server) handleBackfillVectorLinks(w http.ResponseWriter, r *http.Request) {
	topK := 5
	if k := r.URL.Query().Get("top_k"); k != "" {
		fmt.Sscanf(k, "%d", &topK)
	}
	onlyUnlinked := true
	if v := r.URL.Query().Get("only_unlinked"); v == "0" || v == "false" {
		onlyUnlinked = false
	}
	result, err := s.ml.BackfillVectorLinks(topK, onlyUnlinked)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handleSyncMemory(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")

	// TODO: Bidirectional sync with content hash dedup
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"repo":   repo,
		"status": "sync_pending",
		"note":   "Bidirectional memory sync — pending implementation",
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"server":  "devai-shared",
		"version": "0.1.0",
		"mode":    "shared",
	})
}

func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

// --- Graph endpoints ----------------------------------------------------------

// openIndexDB opens the SQLite index.db inside the configured StateDir.
// Caller must close. Returns an error if StateDir is unset or db is missing.
func (s *Server) openIndexDB() (*sql.DB, error) {
	if s.config.StateDir == "" {
		return nil, fmt.Errorf("server StateDir not configured")
	}
	dbPath := filepath.Join(s.config.StateDir, "index.db")
	db, err := sql.Open("sqlite", dbPath+"?mode=ro")
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", dbPath, err)
	}
	return db, nil
}

// handleGraphRepos lists distinct (repo, branch, edge_count) combos in the index.
// Useful as the first call from the UI to populate dropdowns.
func (s *Server) handleGraphRepos(w http.ResponseWriter, r *http.Request) {
	db, err := s.openIndexDB()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer db.Close()

	rows, err := db.Query(`SELECT repo, branch, COUNT(*) FROM graph_edges
	                       GROUP BY repo, branch ORDER BY 3 DESC`)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()

	type repoRow struct {
		Repo   string `json:"repo"`
		Branch string `json:"branch"`
		Edges  int    `json:"edges"`
	}
	out := []repoRow{}
	for rows.Next() {
		var rr repoRow
		if err := rows.Scan(&rr.Repo, &rr.Branch, &rr.Edges); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		out = append(out, rr)
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"repos": out})
}

// handleGetGraph returns nodes+edges for {repo} (or comma-separated list of repos) in Cytoscape format.
// Query params:
//
//	branch  — required (the call graph is huge; we always slice)
//	kind    — calls|imports (optional, default all)
//	limit   — max edges (default 150, hard cap 2000)
//	file    — optional source_file filter (LIKE %file%)
//	hide_external — "1" to drop edges whose target is a fully-qualified external import
func (s *Server) handleGetGraph(w http.ResponseWriter, r *http.Request) {
	repoParam := r.PathValue("repo")
	repos := splitCSV(repoParam)
	if len(repos) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "repo path required"})
		return
	}
	branch := r.URL.Query().Get("branch")
	if branch == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "branch query param required (use GET /api/v1/graph/repos to list valid combos)",
		})
		return
	}
	kind := r.URL.Query().Get("kind")
	file := r.URL.Query().Get("file")
	hideExternal := r.URL.Query().Get("hide_external") == "1"
	hideSynthetic := r.URL.Query().Get("hide_synthetic") == "1"
	limit := 150
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}
	if limit > 2000 {
		limit = 2000
	}

	db, err := s.openIndexDB()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer db.Close()

	placeholders := strings.Repeat("?,", len(repos))
	placeholders = placeholders[:len(placeholders)-1]
	q := "SELECT source, target, kind, source_file, line, repo FROM graph_edges WHERE repo IN (" + placeholders + ") AND branch = ?"
	args := []interface{}{}
	for _, r := range repos {
		args = append(args, r)
	}
	args = append(args, branch)
	if kind != "" {
		q += " AND kind = ?"
		args = append(args, kind)
	}
	if file != "" {
		q += " AND source_file LIKE ?"
		args = append(args, "%"+file+"%")
	}
	q += " ORDER BY source_file, line LIMIT ?"
	args = append(args, limit)

	rows, err := db.Query(q, args...)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()

	nodes, edges := buildCytoscape(rows, hideExternal, hideSynthetic)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"repos":  repos,
		"branch": branch,
		"kind":   kind,
		"limit":  limit,
		"nodes":  nodes,
		"edges":  edges,
	})
}

// handleGraphSearch finds symbols matching a prefix via SQLite FTS5.
// Falls back to LIKE substring matching if the FTS5 table is empty
// (legacy index that hasn't been rebuilt yet).
func (s *Server) handleGraphSearch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	if q == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "q query param required"})
		return
	}
	repos := splitCSV(r.URL.Query().Get("repo"))
	branch := r.URL.Query().Get("branch")

	db, err := s.openIndexDB()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer db.Close()

	useFTS := false
	var ftsRows int
	_ = db.QueryRow("SELECT COUNT(*) FROM graph_symbols_fts").Scan(&ftsRows)
	useFTS = ftsRows > 0

	var sqlStr string
	var args []interface{}
	if useFTS {
		// FTS5 MATCH: prefix search via "term*". We tokenize the query the
		// same way the indexer pre-processes labels — split on non-alnum AND
		// on camelCase boundaries — so 'BirthRecord' becomes
		// 'Birth* AND Record*' and matches indexed 'mapDtoToBirthRecord'.
		tokens := splitForFTS(q)
		matchExpr := strings.Join(tokens, " AND ")
		if matchExpr == "" {
			matchExpr = q + "*"
		}
		sqlStr = `SELECT DISTINCT canonical AS symbol, repo, branch, source_file AS file, line
                  FROM graph_symbols_fts WHERE label MATCH ?`
		args = []interface{}{matchExpr}
	} else {
		sqlStr = `SELECT DISTINCT symbol, repo, branch, file, line FROM (
                  SELECT source AS symbol, repo, branch, source_file AS file, line FROM graph_edges
                  UNION ALL
                  SELECT target AS symbol, repo, branch, source_file AS file, line FROM graph_edges
                ) WHERE symbol LIKE ?`
		args = []interface{}{"%" + q + "%"}
	}
	if len(repos) > 0 {
		ph := strings.Repeat("?,", len(repos))
		ph = ph[:len(ph)-1]
		sqlStr += " AND repo IN (" + ph + ")"
		for _, r := range repos {
			args = append(args, r)
		}
	}
	if branch != "" {
		sqlStr += " AND branch = ?"
		args = append(args, branch)
	}
	sqlStr += " ORDER BY symbol LIMIT 25"

	rows, err := db.Query(sqlStr, args...)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()

	type match struct {
		Symbol string `json:"symbol"`
		Label  string `json:"label"`
		Repo   string `json:"repo"`
		Branch string `json:"branch"`
		File   string `json:"file"`
		Line   int    `json:"line"`
	}
	out := []match{}
	for rows.Next() {
		var m match
		if err := rows.Scan(&m.Symbol, &m.Repo, &m.Branch, &m.File, &m.Line); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		m.Label = shortLabel(m.Symbol)
		out = append(out, m)
	}
	mode := "like"
	if useFTS {
		mode = "fts5"
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"matches": out, "mode": mode})
}

// handleGraphFTSRebuild forces a rebuild of the graph_symbols_fts index.
// Use after switching to a build that supports FTS5, or after a fresh index.
func (s *Server) handleGraphFTSRebuild(w http.ResponseWriter, r *http.Request) {
	force := r.URL.Query().Get("force") == "1"
	result, err := s.ml.FTSRebuild(force)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleGraphImpact returns the impact radius of a symbol: direct callers/callees,
// transitive counts up to depth, affected files and hot files.
//
// Query params (all required except depth/kind):
//
//	symbol  — full symbol id
//	repo    — repository
//	branch  — branch
//	depth   — 1..8 (default 3)
//	kind    — calls|imports|"" (default calls)
func (s *Server) handleGraphImpact(w http.ResponseWriter, r *http.Request) {
	symbol := r.URL.Query().Get("symbol")
	repo := r.URL.Query().Get("repo")
	branch := r.URL.Query().Get("branch")
	if symbol == "" || repo == "" || branch == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "symbol, repo, branch query params are required",
		})
		return
	}
	depth := 3
	if d := r.URL.Query().Get("depth"); d != "" {
		fmt.Sscanf(d, "%d", &depth)
	}
	kind := r.URL.Query().Get("kind")
	if kind == "" {
		kind = "calls"
	}

	result, err := s.ml.ImpactAnalysis(symbol, repo, branch, depth, kind)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// handleGraphSymbol returns the 1-hop neighborhood (callers + callees) of a symbol.
// Used by the click-to-explore flow.
// Query params: symbol (required), repo (csv, optional), branch (required), kind, hide_external.
func (s *Server) handleGraphSymbol(w http.ResponseWriter, r *http.Request) {
	symbol := r.URL.Query().Get("symbol")
	if symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "symbol query param required"})
		return
	}
	branch := r.URL.Query().Get("branch")
	if branch == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "branch query param required"})
		return
	}
	repos := splitCSV(r.URL.Query().Get("repo"))
	kind := r.URL.Query().Get("kind")
	hideExternal := r.URL.Query().Get("hide_external") == "1"
	hideSynthetic := r.URL.Query().Get("hide_synthetic") == "1"

	db, err := s.openIndexDB()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer db.Close()

	sql := `SELECT source, target, kind, source_file, line, repo FROM graph_edges
            WHERE (source = ? OR target = ?) AND branch = ?`
	args := []interface{}{symbol, symbol, branch}
	if len(repos) > 0 {
		ph := strings.Repeat("?,", len(repos))
		ph = ph[:len(ph)-1]
		sql += " AND repo IN (" + ph + ")"
		for _, r := range repos {
			args = append(args, r)
		}
	}
	if kind != "" {
		sql += " AND kind = ?"
		args = append(args, kind)
	}
	sql += " LIMIT 500"

	rows, err := db.Query(sql, args...)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()

	nodes, edges := buildCytoscape(rows, hideExternal, hideSynthetic)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"symbol": symbol,
		"branch": branch,
		"nodes":  nodes,
		"edges":  edges,
	})
}

// --- helpers ------------------------------------------------------------------

func splitCSV(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// externalImportPrefixes are fully-qualified import targets we let the user
// hide to reduce visual noise. Heuristic, not perfect.
var externalImportPrefixes = []string{
	"java.", "javax.", "jakarta.", "org.springframework.", "org.junit.",
	"org.hibernate.", "org.slf4j.", "io.quarkus.", "com.fasterxml.", "lombok.",
	"@angular/", "rxjs/", "primeng/", "@nx/", "react", "next/",
}

func isExternal(symbol string) bool {
	for _, p := range externalImportPrefixes {
		if strings.HasPrefix(symbol, p) {
			return true
		}
	}
	return false
}

// buildCytoscape converts SQL rows into nodes + edges. Expects columns
// (source, target, kind, source_file, line, repo).
// hideSynthetic drops placeholder symbols like `<unknown>`, `<module>` —
// these are the indexer's catch-all for file-level statements, not real
// symbols, and dominate edge counts without adding signal.
func buildCytoscape(rows *sql.Rows, hideExternal, hideSynthetic bool) ([]map[string]interface{}, []map[string]interface{}) {
	type rawEdge struct {
		src, tgt, kind, file, repo string
		line                       int
	}
	raw := []rawEdge{}
	for rows.Next() {
		var e rawEdge
		if err := rows.Scan(&e.src, &e.tgt, &e.kind, &e.file, &e.line, &e.repo); err != nil {
			continue
		}
		raw = append(raw, e)
	}

	type nInfo struct {
		file, repo string
		external   bool
	}
	nodeInfo := map[string]*nInfo{}
	for _, e := range raw {
		if hideExternal && isExternal(e.tgt) {
			continue
		}
		if hideSynthetic && (isSynthetic(e.src) || isSynthetic(e.tgt)) {
			continue
		}
		if _, ok := nodeInfo[e.src]; !ok {
			nodeInfo[e.src] = &nInfo{file: e.file, repo: e.repo, external: isExternal(e.src)}
		} else if nodeInfo[e.src].file == "" {
			nodeInfo[e.src].file = e.file
		}
		if _, ok := nodeInfo[e.tgt]; !ok {
			nodeInfo[e.tgt] = &nInfo{repo: e.repo, external: isExternal(e.tgt)}
		}
	}

	nodes := make([]map[string]interface{}, 0, len(nodeInfo))
	for id, info := range nodeInfo {
		nodes = append(nodes, map[string]interface{}{
			"data": map[string]interface{}{
				"id": id, "label": shortLabel(id),
				"file": info.file, "repo": info.repo, "external": info.external,
			},
		})
	}

	edges := make([]map[string]interface{}, 0, len(raw))
	edgeID := 0
	for _, e := range raw {
		if hideExternal && isExternal(e.tgt) {
			continue
		}
		if hideSynthetic && (isSynthetic(e.src) || isSynthetic(e.tgt)) {
			continue
		}
		edgeID++
		edges = append(edges, map[string]interface{}{
			"data": map[string]interface{}{
				"id":     fmt.Sprintf("e%d", edgeID),
				"source": e.src, "target": e.tgt,
				"kind": e.kind, "file": e.file, "line": e.line,
			},
		})
	}
	return nodes, edges
}

// isSynthetic flags the placeholder symbols the indexer emits for file-level
// statements (Java/JS `<unknown>`, Python `<module>`). They aren't real symbols.
func isSynthetic(id string) bool {
	tail := shortLabel(id)
	return tail == "<unknown>" || tail == "<module>" || tail == "<anonymous>"
}

// splitForFTS tokenizes a query the same way the indexer pre-processes symbols:
// split on every non-alnum char AND at camelCase boundaries (Word|Word and
// XML|Parser). Each token gets the FTS5 prefix marker '*'.
func splitForFTS(q string) []string {
	isAlnum := func(ch rune) bool {
		return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
			(ch >= '0' && ch <= '9') || ch == '_'
	}
	isUpper := func(ch rune) bool { return ch >= 'A' && ch <= 'Z' }
	isLowerOrDigit := func(ch rune) bool {
		return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')
	}

	runes := []rune(q)
	tokens := []string{}
	curr := ""
	flush := func() {
		if curr != "" {
			tokens = append(tokens, curr+"*")
			curr = ""
		}
	}
	for i, ch := range runes {
		if !isAlnum(ch) {
			flush()
			continue
		}
		// camelCase boundary 1: lowercase/digit followed by uppercase
		if i > 0 && isLowerOrDigit(runes[i-1]) && isUpper(ch) {
			flush()
		}
		// camelCase boundary 2: ABCdef → split at ABC | def (last upper before lower)
		if i > 1 && isUpper(runes[i-1]) && isUpper(runes[i-2]) && !isUpper(ch) {
			// move the last char of curr (the boundary upper) into a new token
			if len(curr) > 0 {
				last := curr[len(curr)-1]
				curr = curr[:len(curr)-1]
				flush()
				curr = string(last)
			}
		}
		curr += string(ch)
	}
	flush()
	return tokens
}

// shortLabel keeps node labels readable in the UI: strips path prefix before "::".
func shortLabel(id string) string {
	if i := strings.LastIndex(id, "::"); i >= 0 {
		return id[i+2:]
	}
	if i := strings.LastIndex(id, "/"); i >= 0 {
		return id[i+1:]
	}
	return id
}
