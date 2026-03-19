package storage

import (
	"fmt"
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
		return fmt.Errorf("unknown storage mode: %s", r.config.Mode)
	}
	return nil
}
