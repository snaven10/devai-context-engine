package mlclient

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/snaven10/devai/internal/config"
	"github.com/snaven10/devai/internal/runtime"
)

// StdioClient communicates with the Python ML service via JSON-RPC over stdio.
type StdioClient struct {
	cmd       *exec.Cmd
	stdin     io.WriteCloser
	stdout    *bufio.Reader
	mu        sync.Mutex
	nextID    atomic.Int64
	quiet     bool                // suppress stderr forwarding (for MCP mode)
	extraEnv  []string            // additional env vars for the ML process ("KEY=VALUE")
	stateDir  string              // state directory to pass to ML process (--state-dir)
	model     string              // embedding model key to pass to ML process (--model)
	projectCfg *config.ProjectConfig // optional project config for python resolution
}

// Option configures the client.
type Option func(*StdioClient)

// WithQuiet suppresses ML service log forwarding to stderr.
// Use this when running as MCP server to avoid polluting the MCP transport.
func WithQuiet() Option {
	return func(c *StdioClient) { c.quiet = true }
}

// WithEnv appends extra environment variables to the ML service process.
// Each entry should be in "KEY=VALUE" format. These are merged with the
// current process environment (not replacing it).
func WithEnv(env []string) Option {
	return func(c *StdioClient) { c.extraEnv = env }
}

// WithConfig provides a project configuration for Python binary resolution
// and state directory resolution. If the config has a StateDir set, it will
// be used as the default --state-dir for the ML process.
func WithConfig(cfg *config.ProjectConfig) Option {
	return func(c *StdioClient) {
		c.projectCfg = cfg
		if cfg != nil && cfg.StateDir != "" && c.stateDir == "" {
			c.stateDir = cfg.StateDir
		}
		if cfg != nil && cfg.Embeddings.Model != "" && c.model == "" {
			c.model = cfg.Embeddings.Model
		}
	}
}

// WithStateDir sets the state directory passed to the ML process via --state-dir.
// This takes precedence over the value from WithConfig.
func WithStateDir(dir string) Option {
	return func(c *StdioClient) { c.stateDir = dir }
}

type jsonRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
	ID      int64       `json:"id"`
}

type jsonRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	Result  interface{} `json:"result,omitempty"`
	Error   *rpcError   `json:"error,omitempty"`
	ID      int64       `json:"id"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// NewStdioClient starts the Python ML service and returns a client.
// It waits for the DEVAI_ML_READY signal before returning.
func NewStdioClient(opts ...Option) (*StdioClient, error) {
	// Apply options first so projectCfg is available for FindPython.
	client := &StdioClient{}
	for _, opt := range opts {
		opt(client)
	}

	pythonBin := runtime.FindPython(client.projectCfg)

	args := []string{"-m", "devai_ml.server"}
	if client.stateDir != "" {
		args = append(args, "--state-dir", client.stateDir)
	}
	if client.model != "" {
		args = append(args, "--model", client.model)
	}
	cmd := exec.Command(pythonBin, args...)
	client.cmd = cmd

	// Propagate extra env vars to the ML sidecar process.
	// When cmd.Env is nil, the child inherits the parent's env.
	// When extraEnv is set, we explicitly merge parent env + extras.
	if len(client.extraEnv) > 0 {
		cmd.Env = append(os.Environ(), client.extraEnv...)
	}

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("creating stdin pipe: %w", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("creating stdout pipe: %w", err)
	}

	// Capture stderr to wait for READY signal and forward logs
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("creating stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("starting ML service (%s): %w", pythonBin, err)
	}

	client.stdin = stdin
	client.stdout = bufio.NewReader(stdout)

	// Wait for DEVAI_ML_READY on stderr (model loaded)
	ready := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(stderr)
		for scanner.Scan() {
			line := scanner.Text()
			if !client.quiet {
				fmt.Fprintln(os.Stderr, "[ml] "+line)
			}
			if strings.Contains(line, "DEVAI_ML_READY") {
				ready <- nil
				// Keep draining stderr in background
				for scanner.Scan() {
					if !client.quiet {
						fmt.Fprintln(os.Stderr, "[ml] "+scanner.Text())
					}
				}
				return
			}
		}
		ready <- fmt.Errorf("ML service exited before becoming ready")
	}()

	select {
	case err := <-ready:
		if err != nil {
			cmd.Process.Kill()
			return nil, err
		}
	case <-time.After(120 * time.Second):
		cmd.Process.Kill()
		return nil, fmt.Errorf("ML service startup timed out (120s) — model download may be needed")
	}

	return client, nil
}

// Call sends a JSON-RPC request and waits for the response.
func (c *StdioClient) Call(method string, params interface{}) (interface{}, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	id := c.nextID.Add(1)

	req := jsonRPCRequest{
		JSONRPC: "2.0",
		Method:  method,
		Params:  params,
		ID:      id,
	}

	data, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshaling request: %w", err)
	}

	// Send request
	if _, err := c.stdin.Write(append(data, '\n')); err != nil {
		return nil, fmt.Errorf("writing request: %w", err)
	}

	// Read response
	line, err := c.stdout.ReadBytes('\n')
	if err != nil {
		return nil, fmt.Errorf("reading response: %w", err)
	}

	var resp jsonRPCResponse
	if err := json.Unmarshal(line, &resp); err != nil {
		return nil, fmt.Errorf("unmarshaling response: %w", err)
	}

	if resp.Error != nil {
		return nil, fmt.Errorf("RPC error %d: %s", resp.Error.Code, resp.Error.Message)
	}

	return resp.Result, nil
}

// PushIndex pushes local vectors for a repo+branch to the shared Qdrant store.
func (c *StdioClient) PushIndex(repo, branch string) (interface{}, error) {
	params := map[string]string{"repo": repo}
	if branch != "" {
		params["branch"] = branch
	}
	return c.Call("push_index", params)
}

// PullIndex pulls vectors for a repo+branch from the shared Qdrant store to local.
func (c *StdioClient) PullIndex(repo, branch string) (interface{}, error) {
	params := map[string]string{"repo": repo}
	if branch != "" {
		params["branch"] = branch
	}
	return c.Call("pull_index", params)
}

// SyncIndex performs bidirectional sync between local and shared for a repo+branch.
func (c *StdioClient) SyncIndex(repo, branch string) (interface{}, error) {
	params := map[string]string{"repo": repo}
	if branch != "" {
		params["branch"] = branch
	}
	return c.Call("sync_index", params)
}

// MemoryContext lists recent memories filtered by project/scope (no semantic query).
func (c *StdioClient) MemoryContext(project, scope string, limit int) (interface{}, error) {
	params := map[string]interface{}{"limit": limit}
	if project != "" {
		params["project"] = project
	}
	if scope != "" {
		params["scope"] = scope
	}
	return c.Call("memory_context", params)
}

// Recall searches memories semantically with optional metadata filters.
func (c *StdioClient) Recall(query, scope, memType, project string, limit int) (interface{}, error) {
	params := map[string]interface{}{"query": query, "limit": limit}
	if scope != "" {
		params["scope"] = scope
	}
	if memType != "" {
		params["type"] = memType
	}
	if project != "" {
		params["project"] = project
	}
	return c.Call("recall", params)
}

// Remember persists a memory. Fields map matches the Python remember handler params.
func (c *StdioClient) Remember(fields map[string]interface{}) (interface{}, error) {
	return c.Call("remember", fields)
}

// MemoriesBySymbol returns memories that reference a specific code symbol.
func (c *StdioClient) MemoriesBySymbol(symbol, repo, branch string, limit int) (interface{}, error) {
	p := map[string]interface{}{"symbol": symbol, "limit": limit}
	if repo != "" {
		p["repo"] = repo
	}
	if branch != "" {
		p["branch"] = branch
	}
	return c.Call("memories_by_symbol", p)
}

// MemoriesByFile returns memories that reference a file path.
func (c *StdioClient) MemoriesByFile(file string, limit int) (interface{}, error) {
	return c.Call("memories_by_file", map[string]interface{}{"file": file, "limit": limit})
}

// MemoryRefs returns the junction rows (symbol, file, source) for one memory.
func (c *StdioClient) MemoryRefs(memoryID int) (interface{}, error) {
	return c.Call("memory_refs", map[string]interface{}{"id": memoryID})
}

// ImpactAnalysis traces upstream callers + downstream callees of a symbol.
// depth caps the BFS (1=direct only). kind = "calls" | "imports" | "" (any).
func (c *StdioClient) ImpactAnalysis(symbol, repo, branch string, depth int, kind string) (interface{}, error) {
	return c.Call("impact_analysis", map[string]interface{}{
		"symbol": symbol, "repo": repo, "branch": branch,
		"depth": depth, "kind": kind,
	})
}

// FTSRebuild populates (or rebuilds) the graph_symbols_fts index.
func (c *StdioClient) FTSRebuild(force bool) (interface{}, error) {
	return c.Call("fts_rebuild", map[string]interface{}{"force": force})
}

// ExtractQuarkusRoutes scans the indexed .java files of (repo, branch) and
// persists Quarkus/JAX-RS REST routes. sourceRoot is the absolute on-disk
// path of the repo; pass "" to let the Python side auto-detect.
func (c *StdioClient) ExtractQuarkusRoutes(repo, branch, sourceRoot string) (interface{}, error) {
	p := map[string]interface{}{"repo": repo, "branch": branch}
	if sourceRoot != "" {
		p["source_root"] = sourceRoot
	}
	return c.Call("extract_quarkus_routes", p)
}

// SearchRoutes finds routes matching a path substring + optional filters.
func (c *StdioClient) SearchRoutes(q, framework, httpMethod, repo, branch string, limit int) (interface{}, error) {
	p := map[string]interface{}{"limit": limit}
	if q != "" {
		p["q"] = q
	}
	if framework != "" {
		p["framework"] = framework
	}
	if httpMethod != "" {
		p["http_method"] = httpMethod
	}
	if repo != "" {
		p["repo"] = repo
	}
	if branch != "" {
		p["branch"] = branch
	}
	return c.Call("search_routes", p)
}

// RoutesForHandler returns the route(s) served by a given Java handler symbol.
func (c *StdioClient) RoutesForHandler(handlerSymbol string) (interface{}, error) {
	return c.Call("routes_for_handler", map[string]interface{}{"handler_symbol": handlerSymbol})
}

// SymbolMemoryCounts returns {symbol: count} for the heatmap overlay.
func (c *StdioClient) SymbolMemoryCounts(repo, branch string) (interface{}, error) {
	p := map[string]interface{}{}
	if repo != "" {
		p["repo"] = repo
	}
	if branch != "" {
		p["branch"] = branch
	}
	return c.Call("symbol_memory_counts", p)
}

// BackfillSymbolRefs re-extracts symbol references for every existing memory.
func (c *StdioClient) BackfillSymbolRefs() (interface{}, error) {
	return c.Call("backfill_symbol_refs", map[string]interface{}{})
}

// BackfillVectorLinks bridges unlinked memories to code via vector similarity.
// onlyUnlinked: when true, only processes memories that have no junction rows yet.
func (c *StdioClient) BackfillVectorLinks(topK int, onlyUnlinked bool) (interface{}, error) {
	return c.Call("backfill_vector_links", map[string]interface{}{
		"top_k": topK, "only_unlinked": onlyUnlinked,
	})
}

// Close stops the Python ML service.
func (c *StdioClient) Close() error {
	c.stdin.Close()
	return c.cmd.Wait()
}
