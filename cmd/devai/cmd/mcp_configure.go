package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/spf13/cobra"

	"github.com/snaven10/devai/internal/config"
)

var mcpConfigureCmd = &cobra.Command{
	Use:   "configure",
	Short: "Configure DevAI as an MCP server in AI clients",
	Long: `Auto-configure DevAI as an MCP server for Claude Code and Cursor.
Generates agent instructions and writes MCP server config to the appropriate locations.`,
	RunE: runMCPConfigure,
}

func init() {
	mcpConfigureCmd.Flags().Bool("claude", false, "Configure for Claude Code only")
	mcpConfigureCmd.Flags().Bool("cursor", false, "Configure for Cursor only")
	mcpConfigureCmd.Flags().Bool("all", false, "Configure for all detected clients")
	mcpConfigureCmd.Flags().Bool("show", false, "Show current MCP config without writing")
	mcpConfigureCmd.Flags().Bool("remove", false, "Remove DevAI from MCP configs")

	// Register under the existing server command
	serverCmd.AddCommand(mcpConfigureCmd)
}

// mcpServerEntry represents the MCP server JSON block for DevAI.
type mcpServerEntry struct {
	Type    string            `json:"type"`
	Command string            `json:"command"`
	Args    []string          `json:"args"`
	Env     map[string]string `json:"env,omitempty"`
}

// clientResult tracks what happened for each client.
type clientResult struct {
	name     string
	path     string
	ok       bool
	skipped  bool
	reason   string
	removed  bool
}

func runMCPConfigure(cmd *cobra.Command, args []string) error {
	flagClaude, _ := cmd.Flags().GetBool("claude")
	flagCursor, _ := cmd.Flags().GetBool("cursor")
	flagAll, _ := cmd.Flags().GetBool("all")
	flagShow, _ := cmd.Flags().GetBool("show")
	flagRemove, _ := cmd.Flags().GetBool("remove")

	// If no specific flag, default to --all behavior
	if !flagClaude && !flagCursor && !flagAll {
		flagAll = true
	}

	doClaude := flagClaude || flagAll
	doCursor := flagCursor || flagAll

	// 1. Find devai binary path
	binaryPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding devai binary: %w", err)
	}
	binaryPath, err = filepath.EvalSymlinks(binaryPath)
	if err != nil {
		return fmt.Errorf("resolving binary symlinks: %w", err)
	}

	// On Windows, JSON needs forward slashes
	binaryPathJSON := toForwardSlashes(binaryPath)

	// 2. Detect project config
	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("getting working directory: %w", err)
	}

	projectCfg, stateDir, projectRoot := detectProject(cwd)

	// 3. Build MCP server config
	env := map[string]string{
		"DEVAI_STATE_DIR": toForwardSlashes(stateDir),
	}

	if projectCfg != nil {
		mode := projectCfg.Storage.Mode
		if mode == "shared" || mode == "hybrid" {
			env["DEVAI_STORAGE_MODE"] = mode
			if projectCfg.Storage.QdrantURL != "" {
				env["DEVAI_QDRANT_URL"] = projectCfg.Storage.QdrantURL
			}
			if projectCfg.Storage.QdrantKey != "" {
				env["DEVAI_QDRANT_API_KEY"] = projectCfg.Storage.QdrantKey
			}
		}
	}

	entry := mcpServerEntry{
		Type:    "stdio",
		Command: binaryPathJSON,
		Args:    []string{"server", "mcp"},
		Env:     env,
	}

	// --show: print what would be written and exit
	if flagShow {
		return showConfig(entry, doClaude, doCursor, projectRoot)
	}

	// Print header
	fmt.Println("DevAI MCP Configuration")
	fmt.Println()
	fmt.Printf("  Binary:     %s\n", binaryPath)
	fmt.Printf("  State:      %s\n", stateDir)
	if projectCfg != nil {
		mode := projectCfg.Storage.Mode
		if mode == "" {
			mode = "local"
		}
		extra := ""
		if (mode == "shared" || mode == "hybrid") && projectCfg.Storage.QdrantURL != "" {
			extra = fmt.Sprintf(" (Qdrant: %s)", projectCfg.Storage.QdrantURL)
		}
		fmt.Printf("  Mode:       %s%s\n", mode, extra)
	}
	fmt.Println()

	if flagRemove {
		return removeConfigs(doClaude, doCursor)
	}

	// 4 & 5. Write to client configs
	var results []clientResult

	if doClaude {
		r := writeClaudeConfig(entry)
		results = append(results, r)
	}
	if doCursor {
		r := writeCursorConfig(entry)
		results = append(results, r)
	}

	// 6. Generate AGENT.md
	agentResult := writeAgentMD(projectRoot)
	results = append(results, agentResult)

	// Print results
	fmt.Println("Configured:")
	for _, r := range results {
		if r.ok {
			fmt.Printf("  \xe2\x9c\x93 %-13s (%s)\n", r.name, r.path)
		} else if r.skipped {
			fmt.Printf("  - %-13s skipped: %s\n", r.name, r.reason)
		} else {
			fmt.Printf("  \xe2\x9c\x97 %-13s error: %s\n", r.name, r.reason)
		}
	}

	fmt.Println()
	fmt.Println("Restart your AI client for changes to take effect.")
	return nil
}

// detectProject finds .devai/config.yaml and returns the config, state dir, and project root.
func detectProject(cwd string) (*config.ProjectConfig, string, string) {
	cfgPath := config.FindConfigFile(cwd)
	if cfgPath != "" {
		cfg, err := config.LoadConfig(cfgPath)
		if err == nil {
			// Project root is two levels up from .devai/config.yaml
			projectRoot := filepath.Dir(filepath.Dir(cfgPath))
			stateDir := filepath.Join(projectRoot, ".devai", "state")
			return &cfg, stateDir, projectRoot
		}
	}

	// No project config found — use default state dir
	home, _ := os.UserHomeDir()
	stateDir := filepath.Join(home, ".local", "share", "devai", "state")
	return nil, stateDir, cwd
}

// claudeConfigPath returns the path to Claude Code's settings.json.
func claudeConfigPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".claude", "settings.json")
}

// cursorConfigPath returns the path to Cursor's mcp.json.
func cursorConfigPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".cursor", "mcp.json")
}

// writeClaudeConfig adds/updates the devai entry in Claude Code's settings.json.
func writeClaudeConfig(entry mcpServerEntry) clientResult {
	p := claudeConfigPath()
	return writeMCPToJSON(p, "Claude Code", entry)
}

// writeCursorConfig adds/updates the devai entry in Cursor's mcp.json.
func writeCursorConfig(entry mcpServerEntry) clientResult {
	p := cursorConfigPath()
	return writeMCPToJSON(p, "Cursor", entry)
}

// writeMCPToJSON reads a JSON config file, merges the devai MCP entry, and writes it back.
func writeMCPToJSON(path, clientName string, entry mcpServerEntry) clientResult {
	result := clientResult{name: clientName, path: shortenHome(path)}

	// Ensure parent directory exists
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		result.reason = fmt.Sprintf("creating directory: %v", err)
		return result
	}

	// Read existing config or start fresh
	data := map[string]interface{}{}
	if raw, err := os.ReadFile(path); err == nil {
		if len(raw) > 0 {
			if err := json.Unmarshal(raw, &data); err != nil {
				result.skipped = true
				result.reason = fmt.Sprintf("invalid JSON in %s — not modifying", path)
				return result
			}
		}
	}

	// Ensure mcpServers map exists
	mcpServers, ok := data["mcpServers"].(map[string]interface{})
	if !ok {
		mcpServers = map[string]interface{}{}
	}

	// Convert entry to a map for JSON merge
	entryBytes, _ := json.Marshal(entry)
	var entryMap map[string]interface{}
	json.Unmarshal(entryBytes, &entryMap)

	mcpServers["devai"] = entryMap
	data["mcpServers"] = mcpServers

	// Write back with indentation
	out, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		result.reason = fmt.Sprintf("marshaling JSON: %v", err)
		return result
	}

	if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
		result.reason = fmt.Sprintf("writing file: %v", err)
		return result
	}

	result.ok = true
	return result
}

// removeMCPFromJSON removes the devai key from mcpServers in a JSON config file.
func removeMCPFromJSON(path, clientName string) clientResult {
	result := clientResult{name: clientName, path: shortenHome(path)}

	raw, err := os.ReadFile(path)
	if err != nil {
		result.skipped = true
		result.reason = "config file not found"
		return result
	}

	data := map[string]interface{}{}
	if err := json.Unmarshal(raw, &data); err != nil {
		result.skipped = true
		result.reason = "invalid JSON"
		return result
	}

	mcpServers, ok := data["mcpServers"].(map[string]interface{})
	if !ok {
		result.skipped = true
		result.reason = "no mcpServers key"
		return result
	}

	if _, exists := mcpServers["devai"]; !exists {
		result.skipped = true
		result.reason = "devai not configured"
		return result
	}

	delete(mcpServers, "devai")
	data["mcpServers"] = mcpServers

	out, _ := json.MarshalIndent(data, "", "  ")
	if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
		result.reason = fmt.Sprintf("writing file: %v", err)
		return result
	}

	result.ok = true
	result.removed = true
	return result
}

// removeConfigs removes DevAI from all requested client configs.
func removeConfigs(doClaude, doCursor bool) error {
	fmt.Println("Removing DevAI from MCP configs:")
	fmt.Println()

	if doClaude {
		r := removeMCPFromJSON(claudeConfigPath(), "Claude Code")
		printRemoveResult(r)
	}
	if doCursor {
		r := removeMCPFromJSON(cursorConfigPath(), "Cursor")
		printRemoveResult(r)
	}
	fmt.Println()
	return nil
}

func printRemoveResult(r clientResult) {
	if r.ok {
		fmt.Printf("  \xe2\x9c\x93 %-13s removed from %s\n", r.name, r.path)
	} else if r.skipped {
		fmt.Printf("  - %-13s %s\n", r.name, r.reason)
	} else {
		fmt.Printf("  \xe2\x9c\x97 %-13s error: %s\n", r.name, r.reason)
	}
}

// showConfig prints the MCP config that would be written without actually writing.
func showConfig(entry mcpServerEntry, doClaude, doCursor bool, projectRoot string) error {
	entryJSON, _ := json.MarshalIndent(entry, "  ", "  ")

	fmt.Println("DevAI MCP server config (would be written):")
	fmt.Println()
	fmt.Printf("  %s\n", string(entryJSON))
	fmt.Println()

	if doClaude {
		fmt.Printf("  Claude Code: %s\n", claudeConfigPath())
	}
	if doCursor {
		fmt.Printf("  Cursor:      %s\n", cursorConfigPath())
	}
	fmt.Printf("  Agent guide: %s\n", filepath.Join(projectRoot, ".devai", "AGENT.md"))
	fmt.Println()
	return nil
}

// writeAgentMD generates the .devai/AGENT.md file with AI agent instructions.
func writeAgentMD(projectRoot string) clientResult {
	agentDir := filepath.Join(projectRoot, ".devai")
	agentPath := filepath.Join(agentDir, "AGENT.md")
	result := clientResult{name: "Agent guide", path: ".devai/AGENT.md"}

	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		result.reason = fmt.Sprintf("creating directory: %v", err)
		return result
	}

	content := agentMDContent()
	if err := os.WriteFile(agentPath, []byte(content), 0o644); err != nil {
		result.reason = fmt.Sprintf("writing file: %v", err)
		return result
	}

	result.ok = true
	return result
}

func agentMDContent() string {
	return `# DevAI — AI Agent Instructions

You have access to DevAI tools via MCP. Use these tools instead of manually reading files or searching code.

## When to use DevAI tools

### Code Search — use ` + "`search`" + ` instead of grep/find
When looking for code related to a concept, feature, or pattern:
- ` + "`search(query: \"authentication middleware\", limit: 10)`" + `
- ` + "`search(query: \"database connection pool\", language: \"go\")`" + `
- Supports natural language — describe what you're looking for, not exact keywords

### Build Context — use ` + "`build_context`" + ` for complex questions
When you need comprehensive context about a topic:
- ` + "`build_context(query: \"how does the payment flow work\", max_tokens: 8000)`" + `
- Combines code search + memory recall + dependency analysis
- Returns token-budgeted context ready for analysis

### Read Symbols — use ` + "`read_symbol`" + ` for definitions
When you need a specific function, class, or type definition:
- ` + "`read_symbol(name: \"AuthMiddleware\")`" + `
- Returns the full definition with documentation

### Find References — use ` + "`get_references`" + ` for usage analysis
When you need to know where something is used:
- ` + "`get_references(symbol: \"handleLogin\")`" + `
- Returns all call sites, imports, and usages across repos

### Read Files — use ` + "`read_file`" + ` for specific files
When you need to read a specific file or section:
- ` + "`read_file(path: \"/absolute/path/to/file.go\")`" + `
- ` + "`read_file(path: \"/path/to/file.go\", start_line: 50, end_line: 100)`" + `

### Memory — use ` + "`remember`" + ` and ` + "`recall`" + `
Save important decisions, discoveries, and patterns:
- ` + "`remember(content: \"Auth uses JWT with RS256\", type: \"decision\", project: \"myproject\")`" + `
- ` + "`recall(query: \"authentication\", project: \"myproject\")`" + `

### Indexing — use ` + "`index_repo`" + ` to update index
After significant code changes:
- ` + "`index_repo(path: \"/path/to/repo\", incremental: true)`" + `

## Priority: DevAI tools > manual file reads
1. For code questions: ` + "`search`" + ` or ` + "`build_context`" + ` first
2. For specific symbols: ` + "`read_symbol`" + ` or ` + "`get_references`" + `
3. For specific files: ` + "`read_file`" + `
4. Only fall back to manual file reading if DevAI tools don't return what you need
`
}

// toForwardSlashes converts backslashes to forward slashes for JSON paths on Windows.
func toForwardSlashes(p string) string {
	if runtime.GOOS == "windows" {
		return strings.ReplaceAll(p, "\\", "/")
	}
	return p
}

// shortenHome replaces the home directory prefix with ~ for display.
func shortenHome(p string) string {
	home, err := os.UserHomeDir()
	if err != nil {
		return p
	}
	if strings.HasPrefix(p, home) {
		return "~" + p[len(home):]
	}
	return p
}
