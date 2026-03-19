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
			mcplib.WithDescription("Save a memory entry (architectural insight, design decision, debugging note, etc)."),
			mcplib.WithString("text", mcplib.Required(), mcplib.Description("Memory content to save")),
			mcplib.WithString("type", mcplib.Description("Memory type: insight, decision, note, bug (default: note)")),
			mcplib.WithString("scope", mcplib.Description("Scope: shared (team) or local (personal). Default: shared")),
			mcplib.WithString("tags", mcplib.Description("Comma-separated tags")),
		),
		s.handleRemember,
	)

	// 7. recall
	srv.AddTool(
		mcplib.NewTool("recall",
			mcplib.WithDescription("Search memories using semantic similarity."),
			mcplib.WithString("query", mcplib.Required(), mcplib.Description("Search query")),
			mcplib.WithString("scope", mcplib.Description("Scope: shared, local, or all (default: all)")),
			mcplib.WithString("type", mcplib.Description("Filter by memory type")),
			mcplib.WithNumber("limit", mcplib.Description("Maximum results (default: 5)")),
		),
		s.handleRecall,
	)

	// 8. get_branch_context
	srv.AddTool(
		mcplib.NewTool("get_branch_context",
			mcplib.WithDescription("Get current branch information and index statistics."),
			mcplib.WithString("branch", mcplib.Description("Branch name (default: current active branch)")),
		),
		s.handleGetBranchContext,
	)

	// 9. switch_context
	srv.AddTool(
		mcplib.NewTool("switch_context",
			mcplib.WithDescription("Switch the active search context to a different repository or branch."),
			mcplib.WithString("repo", mcplib.Description("Repository path")),
			mcplib.WithString("branch", mcplib.Description("Branch name")),
		),
		s.handleSwitchContext,
	)

	// 10. get_session_history
	srv.AddTool(
		mcplib.NewTool("get_session_history",
			mcplib.WithDescription("Get recent session activity (queries, tool calls, files accessed)."),
			mcplib.WithNumber("limit", mcplib.Description("Maximum events (default: 20)")),
			mcplib.WithString("type", mcplib.Description("Filter by event type")),
		),
		s.handleGetSessionHistory,
	)

	// 11. index_status
	srv.AddTool(
		mcplib.NewTool("index_status",
			mcplib.WithDescription("Show index freshness and statistics per branch."),
			mcplib.WithString("repo", mcplib.Description("Repository path filter")),
		),
		s.handleIndexStatus,
	)

	// 12. index_repo
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
// mcp-go types Arguments as `any`, so we need a type assertion.
func args(request mcplib.CallToolRequest) map[string]interface{} {
	if m, ok := request.Params.Arguments.(map[string]interface{}); ok {
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

	embedResult, err := s.mlClient.Call("embed", map[string]interface{}{"text": query})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("embedding failed: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"query":    query,
		"embedded": true,
		"result":   embedResult,
		"note":     "Vector store search integration pending",
	}, "", "  ")

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
	maxTokens := argFloat(a, "max_tokens", 4096)

	embedResult, err := s.mlClient.Call("embed", map[string]interface{}{"text": query})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("embedding failed: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"query":      query,
		"max_tokens": maxTokens,
		"embedded":   true,
		"result":     embedResult,
		"note":       "Context assembly from vector search pending",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleReadSymbol(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	name := argString(a, "name", "")

	result, err := s.mlClient.Call("embed", map[string]interface{}{"text": name})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("symbol lookup failed: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"symbol":   name,
		"embedded": true,
		"result":   result,
		"note":     "Symbol index lookup pending",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleGetReferences(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	symbol := argString(a, "symbol", "")

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"symbol": symbol,
		"note":   "Graph store reference lookup pending",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleRemember(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	text := argString(a, "text", "")
	memType := argString(a, "type", "note")
	scope := argString(a, "scope", "shared")
	tags := argString(a, "tags", "")

	_, err := s.mlClient.Call("embed", map[string]interface{}{"text": text})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("embedding memory: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"saved": true,
		"type":  memType,
		"scope": scope,
		"tags":  tags,
		"note":  "Memory storage pending full implementation",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleRecall(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	query := argString(a, "query", "")
	limit := argFloat(a, "limit", 5)

	_, err := s.mlClient.Call("embed", map[string]interface{}{"text": query})
	if err != nil {
		return mcplib.NewToolResultError(fmt.Sprintf("embedding query: %v", err)), nil
	}

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"query": query,
		"limit": limit,
		"note":  "Memory vector search pending",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleGetBranchContext(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	branch := argString(a, "branch", s.activeBranch)

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"branch":      branch,
		"active_repo": s.activeRepo,
		"note":        "Full branch context + index stats pending",
	}, "", "  ")

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

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"limit": limit,
		"note":  "Session history from SQLite pending",
	}, "", "  ")

	return mcplib.NewToolResultText(string(resultJSON)), nil
}

func (s *Server) handleIndexStatus(ctx context.Context, request mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
	a := args(request)
	repo := argString(a, "repo", s.activeRepo)

	resultJSON, _ := json.MarshalIndent(map[string]interface{}{
		"repo": repo,
		"note": "Index state from SQLite pending",
	}, "", "  ")

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
