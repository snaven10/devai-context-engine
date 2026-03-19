package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	mcplib "github.com/mark3labs/mcp-go/mcp"
	mcpserver "github.com/mark3labs/mcp-go/server"

	"github.com/snaven10/devai/internal/mlclient"
)

// Server wraps the MCP server and provides code intelligence tools.
type Server struct {
	mcpServer    *mcpserver.MCPServer
	mlClient     *mlclient.StdioClient
	activeRepo   string
	activeBranch string
}

// New creates a new MCP server with all DevAI tools registered.
func New(mlClient *mlclient.StdioClient) *Server {
	s := &Server{
		mlClient: mlClient,
	}

	mcpSrv := mcpserver.NewMCPServer(
		"devai",
		"0.1.0",
		mcpserver.WithToolCapabilities(true),
	)

	// Register all tools
	s.registerTools(mcpSrv)
	s.mcpServer = mcpSrv
	return s
}

// ServeStdio starts the MCP server on stdin/stdout.
func (s *Server) ServeStdio() error {
	return mcpserver.ServeStdio(s.mcpServer)
}

func (s *Server) registerTools(srv *mcpserver.MCPServer) {
	// 1. search
	srv.AddTool(
		mcplib.NewTool("search",
			mcplib.WithDescription("Semantic search across indexed code. Returns relevant code chunks ranked by similarity."),
			mcplib.WithString("query", mcplib.Required(), mcplib.Description("Natural language search query")),
			mcplib.WithString("repo", mcplib.Description("Repository path filter")),
			mcplib.WithString("branch", mcplib.Description("Branch to search (default: current)")),
			mcplib.WithNumber("limit", mcplib.Description("Maximum results (default: 10)")),
			mcplib.WithString("language", mcplib.Description("Filter by language")),
		),
		s.handleSearch,
	)

	// 2. read_file
	srv.AddTool(
		mcplib.NewTool("read_file",
			mcplib.WithDescription("Read a file's contents with optional line range."),
			mcplib.WithString("path", mcplib.Required(), mcplib.Description("File path to read")),
			mcplib.WithNumber("start_line", mcplib.Description("Start line (1-indexed)")),
			mcplib.WithNumber("end_line", mcplib.Description("End line (inclusive)")),
		),
		s.handleReadFile,
	)

	// 3. build_context
	srv.AddTool(
		mcplib.NewTool("build_context",
			mcplib.WithDescription("Build AI-ready context from the codebase for a given query. Returns relevant code with dependency context, formatted for LLM consumption."),
			mcplib.WithString("query", mcplib.Required(), mcplib.Description("What context is needed")),
			mcplib.WithNumber("max_tokens", mcplib.Description("Token budget (default: 4096)")),
			mcplib.WithString("branch", mcplib.Description("Branch context")),
			mcplib.WithBoolean("include_deps", mcplib.Description("Include dependency graph context (default: true)")),
		),
		s.handleBuildContext,
	)

	// 4. read_symbol
	srv.AddTool(
		mcplib.NewTool("read_symbol",
			mcplib.WithDescription("Get a symbol's definition, code, and documentation."),
			mcplib.WithString("name", mcplib.Required(), mcplib.Description("Symbol name to look up")),
			mcplib.WithString("repo", mcplib.Description("Repository filter")),
			mcplib.WithString("branch", mcplib.Description("Branch context")),
		),
		s.handleReadSymbol,
	)

	// 5. get_references
	srv.AddTool(
		mcplib.NewTool("get_references",
			mcplib.WithDescription("Find all usages of a symbol across the codebase."),
			mcplib.WithString("symbol", mcplib.Required(), mcplib.Description("Symbol name to find references for")),
			mcplib.WithString("repo", mcplib.Description("Repository filter")),
			mcplib.WithString("branch", mcplib.Description("Branch context")),
		),
		s.handleGetReferences,
	)

	// 6. remember
	srv.AddTool(
		mcplib.NewTool("remember",
			mcplib.WithDescription("Save a structured memory entry with rich metadata (architectural insight, design decision, debugging note, etc). Supports topic_key for upserts and content deduplication."),
			mcplib.WithString("content", mcplib.Description("Memory content to save (preferred over text). Structured format: **What**: ... **Why**: ... **Where**: ... **Learned**: ...")),
			mcplib.WithString("text", mcplib.Description("Memory content (alias for content, for backward compat)")),
			mcplib.WithString("title", mcplib.Description("Short searchable title (auto-generated if omitted)")),
			mcplib.WithString("type", mcplib.Description("Memory type: insight, decision, note, bug, architecture, pattern, discovery (default: note)")),
			mcplib.WithString("scope", mcplib.Description("Scope: shared (team) or local (personal). Default: shared")),
			mcplib.WithString("project", mcplib.Description("Project context for scoping memories")),
			mcplib.WithString("topic_key", mcplib.Description("Stable key for upserts (e.g. 'architecture/auth-model'). Same key updates existing memory.")),
			mcplib.WithString("tags", mcplib.Description("Comma-separated tags")),
			mcplib.WithString("files", mcplib.Description("Comma-separated file paths related to this memory")),
			mcplib.WithString("repo", mcplib.Description("Repository path context")),
			mcplib.WithString("branch", mcplib.Description("Branch context")),
		),
		s.handleRemember,
	)

	// 7. recall
	srv.AddTool(
		mcplib.NewTool("recall",
			mcplib.WithDescription("Search memories using hybrid semantic + metadata search. Returns rich metadata including title, topic_key, revision count, and timestamps."),
			mcplib.WithString("query", mcplib.Required(), mcplib.Description("Search query")),
			mcplib.WithString("scope", mcplib.Description("Scope: shared, local, or all (default: all)")),
			mcplib.WithString("type", mcplib.Description("Filter by memory type")),
			mcplib.WithString("project", mcplib.Description("Filter by project context")),
			mcplib.WithNumber("limit", mcplib.Description("Maximum results (default: 10)")),
		),
		s.handleRecall,
	)

	// 8. memory_context
	srv.AddTool(
		mcplib.NewTool("memory_context",
			mcplib.WithDescription("Get recent memories without search — quick context recovery."),
			mcplib.WithString("project", mcplib.Description("Filter by project context")),
			mcplib.WithString("scope", mcplib.Description("Scope: shared or local")),
			mcplib.WithNumber("limit", mcplib.Description("Maximum results (default: 20)")),
		),
		s.handleMemoryContext,
	)

	// 9. memory_stats
	srv.AddTool(
		mcplib.NewTool("memory_stats",
			mcplib.WithDescription("Get memory system statistics: total count, breakdown by type and project."),
		),
		s.handleMemoryStats,
	)

	// 10. get_branch_context
	srv.AddTool(
		mcplib.NewTool("get_branch_context",
			mcplib.WithDescription("Get current branch information and index statistics."),
			mcplib.WithString("branch", mcplib.Description("Branch name (default: current active branch)")),
		),
		s.handleGetBranchContext,
	)

	// 11. switch_context
	srv.AddTool(
		mcplib.NewTool("switch_context",
			mcplib.WithDescription("Switch the active search context to a different repository or branch."),
			mcplib.WithString("repo", mcplib.Description("Repository path")),
			mcplib.WithString("branch", mcplib.Description("Branch name")),
		),
		s.handleSwitchContext,
	)

	// 12. get_session_history
	srv.AddTool(
		mcplib.NewTool("get_session_history",
			mcplib.WithDescription("Get recent session activity (queries, tool calls, files accessed)."),
			mcplib.WithNumber("limit", mcplib.Description("Maximum events (default: 20)")),
			mcplib.WithString("type", mcplib.Description("Filter by event type")),
		),
		s.handleGetSessionHistory,
	)

	// 13. index_status
	srv.AddTool(
		mcplib.NewTool("index_status",
			mcplib.WithDescription("Show index freshness and statistics per branch."),
			mcplib.WithString("repo", mcplib.Description("Repository path filter")),
		),
		s.handleIndexStatus,
	)

	// 14. index_repo
	srv.AddTool(
		mcplib.NewTool("index_repo",
			mcplib.WithDescription("Trigger repository indexing. Supports incremental (only changed files) or full reindex."),
			mcplib.WithString("path", mcplib.Required(), mcplib.Description("Repository path to index")),
			mcplib.WithString("branch", mcplib.Description("Branch to index (default: current)")),
			mcplib.WithBoolean("incremental", mcplib.Description("Incremental index (default: true)")),
		),
		s.handleIndexRepo,
	)
}

// --- Argument helpers ---

// args extracts the arguments map from a CallToolRequest.
func args(request mcplib.CallToolRequest) map[string]interface{} {
	if m := request.GetArguments(); m != nil {
		return m
	}
	return map[string]interface{}{}
}

func argString(a map[string]interface{}, key, fallback string) string {
	if v, ok := a[key].(string); ok && v != "" {
		return v
	}
	return fallback
}

func argFloat(a map[string]interface{}, key string, fallback float64) float64 {
	if v, ok := a[key].(float64); ok {
		return v
	}
	return fallback
}

func argBool(a map[string]interface{}, key string, fallback bool) bool {
	if v, ok := a[key].(bool); ok {
		return v
	}
	return fallback
}

// --- Tool Handlers ---

func (s *Server) handleSearch(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	query := argString(a, "query", "")
	limit := argFloat(a, "limit", 10)
	branch := argString(a, "branch", "")
	language := argString(a, "language", "")
	repo := argString(a, "repo", "")

	params := map[string]interface{}{
		"query": query,
		"limit": int(limit),
	}
	if repo != "" {
		params["repo"] = repo
	}
	if branch != "" {
		params["branch"] = branch
	}
	if language != "" {
		params["language"] = language
	}

	result, err := s.mlClient.Call("search", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("search failed: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleReadFile(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	path := argString(a, "path", "")

	content, err := os.ReadFile(path)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("reading file: %v", err)), nil
	}

	text := string(content)

	if startLine := argFloat(a, "start_line", 0); startLine > 0 {
		lines := strings.Split(text, "\n")
		start := int(startLine) - 1
		if start < 0 {
			start = 0
		}
		end := len(lines)
		if endLine := argFloat(a, "end_line", 0); endLine > 0 {
			end = int(endLine)
		}
		if start >= len(lines) {
			return mcplib.NewToolResultError("start_line exceeds file length"), nil
		}
		if end > len(lines) {
			end = len(lines)
		}
		text = strings.Join(lines[start:end], "\n")
	}

	return mcplib.NewToolResultText(text), nil
}

func (s *Server) handleBuildContext(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	query := argString(a, "query", "")
	maxTokens := int(argFloat(a, "max_tokens", 4096))
	branch := argString(a, "branch", "")

	var contextBuf strings.Builder
	tokensUsed := 0

	// Step 1: Search memories for enrichment
	// Memories provide human knowledge that improves context quality over time
	recallResult, err := s.mlClient.Call("recall", map[string]interface{}{
		"query": query,
		"limit": 5,
	})
	if err == nil {
		if rm, ok := recallResult.(map[string]interface{}); ok {
			if memories, ok := rm["memories"].([]interface{}); ok && len(memories) > 0 {
				// Collect file hints from memories
				var fileHints []string
				for _, mem := range memories {
					mm, ok := mem.(map[string]interface{})
					if !ok {
						continue
					}
					text, _ := mm["text"].(string)
					score, _ := mm["score"].(float64)
					if text != "" && score < 1.5 { // only relevant memories
						note := fmt.Sprintf("// [memory] %s\n\n", text)
						noteTokens := len(note) / 4
						if tokensUsed+noteTokens <= maxTokens {
							contextBuf.WriteString(note)
							tokensUsed += noteTokens
						}
						// Extract file paths mentioned in memory text
						fileHints = append(fileHints, extractFilePaths(text)...)
					}
				}
				// Search for files mentioned in memories (enrichment)
				for _, hint := range fileHints {
					hintResult, err := s.mlClient.Call("search", map[string]interface{}{
						"query": hint,
						"limit": 3,
					})
					if err == nil {
						if hm, ok := hintResult.(map[string]interface{}); ok {
							if hResults, ok := hm["results"].([]interface{}); ok {
								for _, r := range hResults {
									item, ok := r.(map[string]interface{})
									if !ok {
										continue
									}
									text, _ := item["text"].(string)
									file, _ := item["file"].(string)
									startLine, _ := item["start_line"].(float64)
									chunk := fmt.Sprintf("// %s:%g\n%s\n\n", file, startLine, text)
									chunkTokens := len(chunk) / 4
									if tokensUsed+chunkTokens <= maxTokens {
										contextBuf.WriteString(chunk)
										tokensUsed += chunkTokens
									}
								}
							}
						}
					}
				}
			}
		}
	}

	// Step 2: Search for relevant code chunks
	params := map[string]interface{}{
		"query": query,
		"limit": 30, // fetch more to fill remaining budget
	}
	if branch != "" {
		params["branch"] = branch
	}

	result, err := s.mlClient.Call("search", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("search failed: %v", err)), nil
	}

	m, ok := result.(map[string]interface{})
	if !ok {
		return mcplib.NewToolResultError("unexpected search result format"), nil
	}

	results, _ := m["results"].([]interface{})

	// Track files already in context to avoid duplicates
	existingContext := contextBuf.String()

	for _, r := range results {
		item, ok := r.(map[string]interface{})
		if !ok {
			continue
		}
		text, _ := item["text"].(string)
		file, _ := item["file"].(string)
		startLine, _ := item["start_line"].(float64)

		chunk := fmt.Sprintf("// %s:%g\n%s\n\n", file, startLine, text)

		// Skip if this file+line is already in context (from memory enrichment)
		fileRef := fmt.Sprintf("// %s:", file)
		if strings.Contains(existingContext, fileRef) {
			continue
		}

		chunkTokens := len(chunk) / 4
		if tokensUsed+chunkTokens > maxTokens {
			break
		}
		contextBuf.WriteString(chunk)
		tokensUsed += chunkTokens
	}

	return mcplib.NewToolResultText(contextBuf.String()), nil
}

// extractFilePaths finds file-like references in memory text.
// Looks for patterns like "path/to/file.ts" or "SomeComponent" → "some-component".
func extractFilePaths(text string) []string {
	var hints []string
	// Simple heuristic: find words that look like file references
	// e.g. "birth-record.service.ts", "nui-muhlbauer.service"
	words := strings.Fields(text)
	for _, w := range words {
		w = strings.Trim(w, ",.;:()[]{}\"'`")
		// Has a dot and looks like a file
		if strings.Contains(w, ".") && (strings.HasSuffix(w, ".ts") ||
			strings.HasSuffix(w, ".js") ||
			strings.HasSuffix(w, ".java") ||
			strings.HasSuffix(w, ".html") ||
			strings.HasSuffix(w, ".css") ||
			strings.HasSuffix(w, ".service") ||
			strings.HasSuffix(w, ".component") ||
			strings.HasSuffix(w, ".interface")) {
			hints = append(hints, w)
		}
		// Has path separator
		if strings.Contains(w, "/") && len(w) > 3 {
			hints = append(hints, w)
		}
	}
	return hints
}

func (s *Server) handleReadSymbol(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	name := argString(a, "name", "")
	branch := argString(a, "branch", s.activeBranch)
	repo := argString(a, "repo", s.activeRepo)

	params := map[string]interface{}{
		"name": name,
	}
	if branch != "" {
		params["branch"] = branch
	}
	if repo != "" {
		params["repo"] = repo
	}

	result, err := s.mlClient.Call("read_symbol", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("symbol lookup failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleGetReferences(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	symbol := argString(a, "symbol", "")
	branch := argString(a, "branch", s.activeBranch)
	repo := argString(a, "repo", s.activeRepo)

	params := map[string]interface{}{
		"symbol": symbol,
	}
	if branch != "" {
		params["branch"] = branch
	}
	if repo != "" {
		params["repo"] = repo
	}

	result, err := s.mlClient.Call("get_references", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("reference lookup failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleRemember(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)

	params := map[string]interface{}{
		"type":  argString(a, "type", "note"),
		"scope": argString(a, "scope", "shared"),
	}

	// content takes priority over text
	if content := argString(a, "content", ""); content != "" {
		params["content"] = content
	} else {
		params["text"] = argString(a, "text", "")
	}

	// Optional rich metadata fields
	if v := argString(a, "title", ""); v != "" {
		params["title"] = v
	}
	if v := argString(a, "project", ""); v != "" {
		params["project"] = v
	}
	if v := argString(a, "topic_key", ""); v != "" {
		params["topic_key"] = v
	}
	if v := argString(a, "tags", ""); v != "" {
		params["tags"] = v
	}
	if v := argString(a, "files", ""); v != "" {
		params["files"] = v
	}
	if v := argString(a, "repo", ""); v != "" {
		params["repo"] = v
	}
	if v := argString(a, "branch", ""); v != "" {
		params["branch"] = v
	}

	result, err := s.mlClient.Call("remember", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("saving memory: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleRecall(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	query := argString(a, "query", "")
	limit := argFloat(a, "limit", 10)
	scope := argString(a, "scope", "")
	memType := argString(a, "type", "")
	project := argString(a, "project", "")

	params := map[string]interface{}{
		"query": query,
		"limit": int(limit),
	}
	if scope != "" {
		params["scope"] = scope
	}
	if memType != "" {
		params["type"] = memType
	}
	if project != "" {
		params["project"] = project
	}

	result, err := s.mlClient.Call("recall", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("recalling memory: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleMemoryContext(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	project := argString(a, "project", "")
	scope := argString(a, "scope", "")
	limit := argFloat(a, "limit", 20)

	params := map[string]interface{}{
		"limit": int(limit),
	}
	if project != "" {
		params["project"] = project
	}
	if scope != "" {
		params["scope"] = scope
	}

	result, err := s.mlClient.Call("memory_context", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("memory context failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleMemoryStats(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	result, err := s.mlClient.Call("memory_stats", map[string]interface{}{})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("memory stats failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleGetBranchContext(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	branch := argString(a, "branch", s.activeBranch)
	repo := argString(a, "repo", s.activeRepo)

	params := map[string]interface{}{}
	if branch != "" {
		params["branch"] = branch
	}
	if repo != "" {
		params["repo"] = repo
	}

	result, err := s.mlClient.Call("get_branch_context", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("branch context failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleSwitchContext(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	if repo := argString(a, "repo", ""); repo != "" {
		s.activeRepo = repo
	}
	if branch := argString(a, "branch", ""); branch != "" {
		s.activeBranch = branch
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"active_repo":   s.activeRepo,
		"active_branch": s.activeBranch,
		"switched":      true,
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleGetSessionHistory(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	limit := argFloat(a, "limit", 20)

	result, err := s.mlClient.Call("get_session_history", map[string]interface{}{
		"limit": int(limit),
	})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("session history failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleIndexStatus(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	repo := argString(a, "repo", s.activeRepo)

	params := map[string]interface{}{}
	if repo != "" {
		params["repo"] = repo
	}

	result, err := s.mlClient.Call("index_status", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("index status failed: %v", err)), nil
	}
	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleIndexRepo(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	path := argString(a, "path", "")
	incremental := argBool(a, "incremental", true)

	params := map[string]interface{}{
		"repo_path":   path,
		"incremental": incremental,
	}
	if branch := argString(a, "branch", ""); branch != "" {
		params["branch"] = branch
	}

	result, err := s.mlClient.Call("index_repo", params)
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("indexing failed: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(result, "", "  ")
	return mcplib.NewToolResultText(string(resultJSON)), nil
}
