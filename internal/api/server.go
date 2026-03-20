package api

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/snaven10/devai/internal/session"
	"github.com/snaven10/devai/internal/storage"
)

// Config holds API server configuration.
type Config struct {
	Addr     string
	APIToken string // simple token auth for now
}

// Server is the shared mode HTTP API server.
type Server struct {
	config       Config
	sessionStore *session.Store
	mux          *http.ServeMux
}

// New creates a new API server.
func New(cfg Config, sessionStore *session.Store) *Server {
	s := &Server{
		config:       cfg,
		sessionStore: sessionStore,
		mux:          http.NewServeMux(),
	}
	s.routes()
	return s
}

// Start begins listening for requests.
func (s *Server) Start() error {
	srv := &http.Server{
		Addr:         s.config.Addr,
		Handler:      s.mux,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second,
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
	s.mux.HandleFunc("GET /api/v1/status", s.authMiddleware(s.handleStatus))
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

func (s *Server) handleGetMemories(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")
	scope := r.URL.Query().Get("scope")
	memType := r.URL.Query().Get("type")

	memories, err := s.sessionStore.SearchMemories("", scope, memType, 50)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"repo":     repo,
		"count":    len(memories),
		"memories": memories,
	})
}

func (s *Server) handlePostMemory(w http.ResponseWriter, r *http.Request) {
	repo := r.PathValue("repo")

	var mem session.Memory
	if err := json.NewDecoder(r.Body).Decode(&mem); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	mem.RepoPath = repo

	id, err := s.sessionStore.SaveMemory(mem)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"id":   id,
		"repo": repo,
	})
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
