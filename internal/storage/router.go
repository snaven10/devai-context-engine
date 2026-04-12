package storage

import (
	"fmt"
	"os"
	"strings"

	"github.com/snaven10/devai/internal/config"
)

// Mode determines where data is stored.
type Mode string

const (
	ModeLocal  Mode = "local"
	ModeShared Mode = "shared"
	ModeHybrid Mode = "hybrid" // local + shared
)

// Config holds storage routing configuration.
type Config struct {
	Mode      Mode
	LocalPath string // path to .devai/state/
	SharedURL string // URL of shared API server
	APIToken  string
}

// Router directs storage operations to the appropriate backend.
type Router struct {
	config Config
}

// New creates a new storage router.
func New(cfg Config) *Router {
	return &Router{config: cfg}
}

// IsLocal returns true if local storage is enabled.
func (r *Router) IsLocal() bool {
	return r.config.Mode == ModeLocal || r.config.Mode == ModeHybrid
}

// IsShared returns true if shared storage is enabled.
func (r *Router) IsShared() bool {
	return r.config.Mode == ModeShared || r.config.Mode == ModeHybrid
}

// LocalPath returns the local state directory.
func (r *Router) LocalPath() string {
	return r.config.LocalPath
}

// SharedURL returns the shared API server URL.
func (r *Router) SharedURL() string {
	return r.config.SharedURL
}

// Validate checks the configuration.
func (r *Router) Validate() error {
	switch r.config.Mode {
	case ModeLocal:
		if r.config.LocalPath == "" {
			return fmt.Errorf("local mode requires a local path")
		}
	case ModeShared:
		if r.config.SharedURL == "" {
			return fmt.Errorf("shared mode requires a server URL")
		}
		if r.config.APIToken == "" {
			return fmt.Errorf("shared mode requires an API token")
		}
	case ModeHybrid:
		if r.config.LocalPath == "" {
			return fmt.Errorf("hybrid mode requires a local path")
		}
		if r.config.SharedURL == "" {
			return fmt.Errorf("hybrid mode requires a server URL")
		}
	default:
		return fmt.Errorf("unknown storage mode: %s. Valid modes: local, shared, hybrid", r.config.Mode)
	}
	return nil
}

// NewFromEnv creates a Router from environment variables.
//
// Reads: DEVAI_STORAGE_MODE (case-insensitive, default "local"),
// DEVAI_QDRANT_URL, DEVAI_QDRANT_API_KEY, DEVAI_LOCAL_DB_PATH.
func NewFromEnv() (*Router, error) {
	mode := Mode(strings.ToLower(os.Getenv("DEVAI_STORAGE_MODE")))
	if mode == "" {
		mode = ModeLocal
	}

	localPath := os.Getenv("DEVAI_LOCAL_DB_PATH")
	if localPath == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return nil, fmt.Errorf("cannot determine home directory: %w", err)
		}
		localPath = home + "/.local/share/devai/state/vectors"
	}

	cfg := Config{
		Mode:      mode,
		LocalPath: localPath,
		SharedURL: os.Getenv("DEVAI_QDRANT_URL"),
		APIToken:  os.Getenv("DEVAI_QDRANT_API_KEY"),
	}

	r := New(cfg)
	if err := r.Validate(); err != nil {
		return nil, err
	}
	return r, nil
}

// EnvVars returns environment variables to propagate to the ML sidecar process.
func (r *Router) EnvVars() []string {
	return []string{
		"DEVAI_STORAGE_MODE=" + string(r.config.Mode),
		"DEVAI_QDRANT_URL=" + r.config.SharedURL,
		"DEVAI_QDRANT_API_KEY=" + r.config.APIToken,
		"DEVAI_LOCAL_DB_PATH=" + r.config.LocalPath,
	}
}

// Mode returns the current storage mode.
func (r *Router) Mode() Mode {
	return r.config.Mode
}

// NewFromConfigWithEnvOverride creates a Router from a ProjectConfig, allowing
// environment variables to override config file values. Priority:
// env var (if non-empty) > config file value > state_dir-derived > default.
func NewFromConfigWithEnvOverride(projectCfg config.ProjectConfig) (*Router, error) {
	// Start with config file values.
	mode := projectCfg.Storage.Mode
	qdrantURL := projectCfg.Storage.QdrantURL
	qdrantKey := projectCfg.Storage.QdrantKey
	localPath := projectCfg.Storage.LocalDBPath

	// Env vars override if non-empty.
	if v := os.Getenv("DEVAI_STORAGE_MODE"); v != "" {
		mode = v
	}
	if v := os.Getenv("DEVAI_QDRANT_URL"); v != "" {
		qdrantURL = v
	}
	if v := os.Getenv("DEVAI_QDRANT_API_KEY"); v != "" {
		qdrantKey = v
	}
	if v := os.Getenv("DEVAI_LOCAL_DB_PATH"); v != "" {
		localPath = v
	}

	// Defaults if both config and env are empty.
	resolvedMode := Mode(strings.ToLower(mode))
	if resolvedMode == "" {
		resolvedMode = ModeLocal
	}

	if localPath == "" {
		// If state_dir is set in config, derive vectors path from it
		if projectCfg.StateDir != "" {
			localPath = projectCfg.StateDir + "/vectors"
		} else {
			home, err := os.UserHomeDir()
			if err != nil {
				return nil, fmt.Errorf("cannot determine home directory: %w", err)
			}
			localPath = home + "/.local/share/devai/state/vectors"
		}
	}

	cfg := Config{
		Mode:      resolvedMode,
		LocalPath: localPath,
		SharedURL: qdrantURL,
		APIToken:  qdrantKey,
	}

	r := New(cfg)
	if err := r.Validate(); err != nil {
		return nil, err
	}
	return r, nil
}

// EnvVarsFromEnv reads storage-related environment variables and returns them
// in "KEY=VALUE" format for propagation to child processes. This is a
// convenience function that does NOT require a Router instance or validation —
// it simply forwards whatever the parent process has set.
func EnvVarsFromEnv() []string {
	keys := []string{
		"DEVAI_STORAGE_MODE",
		"DEVAI_QDRANT_URL",
		"DEVAI_QDRANT_API_KEY",
		"DEVAI_LOCAL_DB_PATH",
	}
	var env []string
	for _, k := range keys {
		if v := os.Getenv(k); v != "" {
			env = append(env, k+"="+v)
		}
	}
	return env
}
