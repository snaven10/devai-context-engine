package storage

import (
	"os"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// NewFromEnv
// ---------------------------------------------------------------------------

func TestNewFromEnv_DefaultMode(t *testing.T) {
	clearStorageEnv(t)

	r, err := NewFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if r.Mode() != ModeLocal {
		t.Errorf("expected mode %q, got %q", ModeLocal, r.Mode())
	}
}

func TestNewFromEnv_SharedMode(t *testing.T) {
	clearStorageEnv(t)
	t.Setenv("DEVAI_STORAGE_MODE", "shared")
	t.Setenv("DEVAI_QDRANT_URL", "qdrant.example.com:6334")
	t.Setenv("DEVAI_QDRANT_API_KEY", "secret")

	r, err := NewFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if r.Mode() != ModeShared {
		t.Errorf("expected mode %q, got %q", ModeShared, r.Mode())
	}
}

func TestNewFromEnv_HybridMode(t *testing.T) {
	clearStorageEnv(t)
	t.Setenv("DEVAI_STORAGE_MODE", "hybrid")
	t.Setenv("DEVAI_QDRANT_URL", "qdrant:6334")
	t.Setenv("DEVAI_LOCAL_DB_PATH", "/tmp/devai")

	r, err := NewFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if r.Mode() != ModeHybrid {
		t.Errorf("expected mode %q, got %q", ModeHybrid, r.Mode())
	}
}

func TestNewFromEnv_CaseInsensitive(t *testing.T) {
	cases := []struct {
		input    string
		expected Mode
	}{
		{"SHARED", ModeShared},
		{"Shared", ModeShared},
		{"LOCAL", ModeLocal},
		{"Hybrid", ModeHybrid},
	}

	for _, tc := range cases {
		t.Run(tc.input, func(t *testing.T) {
			clearStorageEnv(t)
			t.Setenv("DEVAI_STORAGE_MODE", tc.input)
			// Set required vars for shared/hybrid
			t.Setenv("DEVAI_QDRANT_URL", "qdrant:6334")
			t.Setenv("DEVAI_QDRANT_API_KEY", "key")
			t.Setenv("DEVAI_LOCAL_DB_PATH", "/tmp/devai")

			r, err := NewFromEnv()
			if err != nil {
				t.Fatalf("unexpected error for %q: %v", tc.input, err)
			}
			if r.Mode() != tc.expected {
				t.Errorf("input %q: expected %q, got %q", tc.input, tc.expected, r.Mode())
			}
		})
	}
}

func TestNewFromEnv_SharedMissingURL(t *testing.T) {
	clearStorageEnv(t)
	t.Setenv("DEVAI_STORAGE_MODE", "shared")
	// No DEVAI_QDRANT_URL set

	_, err := NewFromEnv()
	if err == nil {
		t.Fatal("expected error for shared mode without URL")
	}
	if !strings.Contains(err.Error(), "server URL") {
		t.Errorf("error should mention server URL, got: %v", err)
	}
}

func TestNewFromEnv_UnknownMode(t *testing.T) {
	clearStorageEnv(t)
	t.Setenv("DEVAI_STORAGE_MODE", "distributed")

	_, err := NewFromEnv()
	if err == nil {
		t.Fatal("expected error for unknown mode")
	}
	if !strings.Contains(err.Error(), "distributed") {
		t.Errorf("error should contain mode name, got: %v", err)
	}
	if !strings.Contains(err.Error(), "local, shared, hybrid") {
		t.Errorf("error should list valid modes, got: %v", err)
	}
}

func TestNewFromEnv_DefaultLocalPath(t *testing.T) {
	clearStorageEnv(t)
	// Don't set DEVAI_LOCAL_DB_PATH — should default to ~/.local/share/devai/state

	r, err := NewFromEnv()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	home, _ := os.UserHomeDir()
	expected := home + "/.local/share/devai/state"
	if r.LocalPath() != expected {
		t.Errorf("expected default path %q, got %q", expected, r.LocalPath())
	}
}

// ---------------------------------------------------------------------------
// EnvVars
// ---------------------------------------------------------------------------

func TestEnvVars_ReturnsKeyValuePairs(t *testing.T) {
	r := New(Config{
		Mode:      ModeShared,
		LocalPath: "/tmp/local",
		SharedURL: "qdrant:6334",
		APIToken:  "tok",
	})

	envs := r.EnvVars()

	expected := map[string]string{
		"DEVAI_STORAGE_MODE":  "shared",
		"DEVAI_QDRANT_URL":    "qdrant:6334",
		"DEVAI_QDRANT_API_KEY": "tok",
		"DEVAI_LOCAL_DB_PATH": "/tmp/local",
	}

	for _, kv := range envs {
		parts := strings.SplitN(kv, "=", 2)
		if len(parts) != 2 {
			t.Errorf("invalid env var format: %q", kv)
			continue
		}
		if want, ok := expected[parts[0]]; ok {
			if parts[1] != want {
				t.Errorf("%s: expected %q, got %q", parts[0], want, parts[1])
			}
			delete(expected, parts[0])
		}
	}

	if len(expected) > 0 {
		t.Errorf("missing env vars: %v", expected)
	}
}

// ---------------------------------------------------------------------------
// EnvVarsFromEnv
// ---------------------------------------------------------------------------

func TestEnvVarsFromEnv_ReadsOsEnv(t *testing.T) {
	clearStorageEnv(t)
	t.Setenv("DEVAI_STORAGE_MODE", "hybrid")
	t.Setenv("DEVAI_QDRANT_URL", "qdrant:6334")

	envs := EnvVarsFromEnv()

	found := map[string]bool{}
	for _, kv := range envs {
		parts := strings.SplitN(kv, "=", 2)
		found[parts[0]] = true
	}

	if !found["DEVAI_STORAGE_MODE"] {
		t.Error("expected DEVAI_STORAGE_MODE in output")
	}
	if !found["DEVAI_QDRANT_URL"] {
		t.Error("expected DEVAI_QDRANT_URL in output")
	}
	// Unset vars should NOT appear
	if found["DEVAI_QDRANT_API_KEY"] {
		t.Error("DEVAI_QDRANT_API_KEY should not appear when unset")
	}
}

func TestEnvVarsFromEnv_EmptyWhenNoVarsSet(t *testing.T) {
	clearStorageEnv(t)

	envs := EnvVarsFromEnv()
	if len(envs) != 0 {
		t.Errorf("expected empty slice, got %v", envs)
	}
}

// ---------------------------------------------------------------------------
// Validate
// ---------------------------------------------------------------------------

func TestValidate_LocalMissingPath(t *testing.T) {
	r := New(Config{Mode: ModeLocal, LocalPath: ""})
	err := r.Validate()
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "local path") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestValidate_SharedMissingToken(t *testing.T) {
	r := New(Config{Mode: ModeShared, SharedURL: "qdrant:6334", APIToken: ""})
	err := r.Validate()
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "API token") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestValidate_HybridMissingLocalPath(t *testing.T) {
	r := New(Config{Mode: ModeHybrid, SharedURL: "qdrant:6334", LocalPath: ""})
	err := r.Validate()
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "local path") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestValidate_HybridMissingSharedURL(t *testing.T) {
	r := New(Config{Mode: ModeHybrid, LocalPath: "/tmp/db", SharedURL: ""})
	err := r.Validate()
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "server URL") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestValidate_UnknownMode(t *testing.T) {
	r := New(Config{Mode: "distributed"})
	err := r.Validate()
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "distributed") {
		t.Errorf("error should contain mode name: %v", err)
	}
}

func TestValidate_LocalWithPath_OK(t *testing.T) {
	r := New(Config{Mode: ModeLocal, LocalPath: "/tmp/devai"})
	if err := r.Validate(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidate_SharedComplete_OK(t *testing.T) {
	r := New(Config{Mode: ModeShared, SharedURL: "qdrant:6334", APIToken: "tok"})
	if err := r.Validate(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidate_HybridComplete_OK(t *testing.T) {
	r := New(Config{Mode: ModeHybrid, LocalPath: "/tmp/db", SharedURL: "qdrant:6334"})
	if err := r.Validate(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func clearStorageEnv(t *testing.T) {
	t.Helper()
	for _, k := range []string{
		"DEVAI_STORAGE_MODE",
		"DEVAI_QDRANT_URL",
		"DEVAI_QDRANT_API_KEY",
		"DEVAI_LOCAL_DB_PATH",
	} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
}
