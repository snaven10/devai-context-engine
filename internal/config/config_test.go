package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfig_ValidYAML(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")

	yaml := `project:
  name: my-repo
  path: /home/user/my-repo
embeddings:
  provider: local
  model: minilm-l6
storage:
  mode: shared
  qdrant_url: qdrant.example.com:6334
  qdrant_api_key: secret-key
  local_db_path: /tmp/devai/state
indexing:
  exclude:
    - "node_modules/**"
    - "vendor/**"
`
	if err := os.WriteFile(cfgPath, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if cfg.Project.Name != "my-repo" {
		t.Errorf("project.name: expected %q, got %q", "my-repo", cfg.Project.Name)
	}
	if cfg.Project.Path != "/home/user/my-repo" {
		t.Errorf("project.path: expected %q, got %q", "/home/user/my-repo", cfg.Project.Path)
	}
	if cfg.Embeddings.Provider != "local" {
		t.Errorf("embeddings.provider: expected %q, got %q", "local", cfg.Embeddings.Provider)
	}
	if cfg.Storage.Mode != "shared" {
		t.Errorf("storage.mode: expected %q, got %q", "shared", cfg.Storage.Mode)
	}
	if cfg.Storage.QdrantURL != "qdrant.example.com:6334" {
		t.Errorf("storage.qdrant_url: expected %q, got %q", "qdrant.example.com:6334", cfg.Storage.QdrantURL)
	}
	if cfg.Storage.QdrantKey != "secret-key" {
		t.Errorf("storage.qdrant_api_key: expected %q, got %q", "secret-key", cfg.Storage.QdrantKey)
	}
	if cfg.Storage.LocalDBPath != "/tmp/devai/state" {
		t.Errorf("storage.local_db_path: expected %q, got %q", "/tmp/devai/state", cfg.Storage.LocalDBPath)
	}
	if len(cfg.Indexing.Exclude) != 2 {
		t.Errorf("indexing.exclude: expected 2 items, got %d", len(cfg.Indexing.Exclude))
	}
}

func TestLoadConfig_FileNotFound(t *testing.T) {
	_, err := LoadConfig("/nonexistent/config.yaml")
	if err == nil {
		t.Fatal("expected error for missing file")
	}
}

func TestLoadConfig_InvalidYAML(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")

	if err := os.WriteFile(cfgPath, []byte(":::invalid"), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := LoadConfig(cfgPath)
	if err == nil {
		t.Fatal("expected error for invalid YAML")
	}
}

func TestFindConfigFile_WalksUp(t *testing.T) {
	// Create: tmpdir/.devai/config.yaml
	// Search from: tmpdir/sub/deep
	root := t.TempDir()
	devaiDir := filepath.Join(root, ".devai")
	os.MkdirAll(devaiDir, 0o755)

	cfgPath := filepath.Join(devaiDir, "config.yaml")
	os.WriteFile(cfgPath, []byte("project:\n  name: test\n"), 0o644)

	searchDir := filepath.Join(root, "sub", "deep")
	os.MkdirAll(searchDir, 0o755)

	found := FindConfigFile(searchDir)
	if found == "" {
		t.Fatal("expected to find config file")
	}

	absCfg, _ := filepath.Abs(cfgPath)
	if found != absCfg {
		t.Errorf("expected %q, got %q", absCfg, found)
	}
}

func TestFindConfigFile_NotFound(t *testing.T) {
	dir := t.TempDir()
	found := FindConfigFile(dir)
	if found != "" {
		t.Errorf("expected empty string, got %q", found)
	}
}

func TestLoadConfigFromCWD_NoConfigFile(t *testing.T) {
	// Change to a temp dir with no .devai/config.yaml
	dir := t.TempDir()
	origDir, _ := os.Getwd()
	os.Chdir(dir)
	defer os.Chdir(origDir)

	cfg, err := LoadConfigFromCWD()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Zero-value config
	if cfg.Project.Name != "" {
		t.Errorf("expected empty project name, got %q", cfg.Project.Name)
	}
	if cfg.Storage.Mode != "" {
		t.Errorf("expected empty storage mode, got %q", cfg.Storage.Mode)
	}
}

func TestLoadConfig_StorageFieldsParsed(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")

	yaml := `storage:
  mode: hybrid
  qdrant_url: qdrant:6334
  qdrant_api_key: my-key
  local_db_path: /data/devai
`
	os.WriteFile(cfgPath, []byte(yaml), 0o644)

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if cfg.Storage.Mode != "hybrid" {
		t.Errorf("mode: expected %q, got %q", "hybrid", cfg.Storage.Mode)
	}
	if cfg.Storage.QdrantURL != "qdrant:6334" {
		t.Errorf("qdrant_url: expected %q, got %q", "qdrant:6334", cfg.Storage.QdrantURL)
	}
	if cfg.Storage.QdrantKey != "my-key" {
		t.Errorf("qdrant_api_key: expected %q, got %q", "my-key", cfg.Storage.QdrantKey)
	}
	if cfg.Storage.LocalDBPath != "/data/devai" {
		t.Errorf("local_db_path: expected %q, got %q", "/data/devai", cfg.Storage.LocalDBPath)
	}
}
