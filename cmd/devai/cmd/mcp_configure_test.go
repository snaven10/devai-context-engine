package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseEnvPairs(t *testing.T) {
	got, err := parseEnvPairs([]string{"DEVAI_EMBEDDING_MODEL=ml-mpnet", "X=a=b"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got["DEVAI_EMBEDDING_MODEL"] != "ml-mpnet" {
		t.Errorf("model = %q, want ml-mpnet", got["DEVAI_EMBEDDING_MODEL"])
	}
	if got["X"] != "a=b" {
		t.Errorf("X = %q, want a=b (only first = splits)", got["X"])
	}
	if _, err := parseEnvPairs([]string{"NOEQUALS"}); err == nil {
		t.Error("expected error for pair without '='")
	}
	if _, err := parseEnvPairs([]string{"=value"}); err == nil {
		t.Error("expected error for empty key")
	}
}

func TestClaudeConfigPath(t *testing.T) {
	p := claudeConfigPath()
	if !strings.HasSuffix(p, ".claude.json") {
		t.Errorf("claudeConfigPath() = %q, want path ending in .claude.json", p)
	}
	if strings.Contains(p, "settings.json") {
		t.Errorf("claudeConfigPath() = %q, must NOT contain settings.json (Claude Code ignores mcpServers there)", p)
	}
}

func TestWriteMCPToJSONNonDestructive(t *testing.T) {
	tmp := t.TempDir()
	path := filepath.Join(tmp, ".claude.json")

	initial := map[string]interface{}{
		"oauthAccount": map[string]interface{}{"user": "alice"},
		"projects":     map[string]interface{}{"p": 1},
		"mcpServers":   map[string]interface{}{"other": map[string]interface{}{"command": "x"}},
	}
	raw, _ := json.Marshal(initial)
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatalf("setup: %v", err)
	}

	entry := mcpServerEntry{Type: "stdio", Command: "/usr/local/bin/devai", Args: []string{"server", "mcp"}}
	r := writeMCPToJSON(path, "Claude Code", entry)
	if !r.ok {
		t.Fatalf("writeMCPToJSON failed: %s", r.reason)
	}

	out, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading result: %v", err)
	}
	var result map[string]interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		t.Fatalf("unmarshal result: %v", err)
	}

	// Pre-existing top-level keys survive
	if oauth, ok := result["oauthAccount"].(map[string]interface{}); !ok || oauth["user"] != "alice" {
		t.Error("oauthAccount was destroyed or mutated")
	}
	if projects, ok := result["projects"].(map[string]interface{}); !ok {
		t.Error("projects key was destroyed")
	} else if projects["p"] != float64(1) {
		t.Errorf("projects[p] = %v, want 1", projects["p"])
	}

	mcp, ok := result["mcpServers"].(map[string]interface{})
	if !ok {
		t.Fatal("mcpServers missing from result")
	}
	// Pre-existing sibling MCP entry survives
	if _, exists := mcp["other"]; !exists {
		t.Error("mcpServers.other was deleted")
	}
	// New devai entry was added
	if _, exists := mcp["devai"]; !exists {
		t.Error("mcpServers.devai was NOT added")
	}
}

func TestResolveClaudeTarget(t *testing.T) {
	root := "/work/myrepo"
	proj := resolveClaudeTarget("project", root)
	if proj != filepath.Join(root, ".mcp.json") {
		t.Errorf("project target = %q, want %q", proj, filepath.Join(root, ".mcp.json"))
	}
	global := resolveClaudeTarget("global", root)
	if global != claudeConfigPath() {
		t.Errorf("global target = %q, want claudeConfigPath() %q", global, claudeConfigPath())
	}
	if resolveClaudeTarget("", root) != claudeConfigPath() {
		t.Error("empty scope should fall back to global")
	}
}
