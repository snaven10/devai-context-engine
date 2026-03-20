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

// WithConfig provides a project configuration for Python binary resolution.
func WithConfig(cfg *config.ProjectConfig) Option {
	return func(c *StdioClient) { c.projectCfg = cfg }
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

	cmd := exec.Command(pythonBin, "-m", "devai_ml.server")
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

// Close stops the Python ML service.
func (c *StdioClient) Close() error {
	c.stdin.Close()
	return c.cmd.Wait()
}
