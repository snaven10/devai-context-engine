package mlclient

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// StdioClient communicates with the Python ML service via JSON-RPC over stdio.
type StdioClient struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	stdout *bufio.Reader
	mu     sync.Mutex
	nextID atomic.Int64
	quiet  bool // suppress stderr forwarding (for MCP mode)
}

// Option configures the client.
type Option func(*StdioClient)

// WithQuiet suppresses ML service log forwarding to stderr.
// Use this when running as MCP server to avoid polluting the MCP transport.
func WithQuiet() Option {
	return func(c *StdioClient) { c.quiet = true }
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

// findPython locates the Python binary. Priority:
// 1. DEVAI_PYTHON env var (explicit override)
// 2. .devai/state/../../ml/.venv/bin/python (project venv)
// 3. ml/.venv/bin/python (relative to cwd or devai binary)
// 4. python3 (system fallback)
func findPython() string {
	// 1. Explicit env var
	if p := os.Getenv("DEVAI_PYTHON"); p != "" {
		return p
	}

	// 2. Find ml/.venv relative to the devai binary
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		// Binary might be at repo root or in a bin/ dir
		candidates := []string{
			filepath.Join(exeDir, "ml", ".venv", "bin", "python"),
			filepath.Join(exeDir, "..", "ml", ".venv", "bin", "python"),
		}
		for _, c := range candidates {
			if _, err := os.Stat(c); err == nil {
				return c
			}
		}
	}

	// 3. Relative to cwd
	if cwd, err := os.Getwd(); err == nil {
		venvPython := filepath.Join(cwd, "ml", ".venv", "bin", "python")
		if _, err := os.Stat(venvPython); err == nil {
			return venvPython
		}
	}

	// 4. System fallback
	return "python3"
}

// NewStdioClient starts the Python ML service and returns a client.
// It waits for the DEVAI_ML_READY signal before returning.
func NewStdioClient(opts ...Option) (*StdioClient, error) {
	pythonBin := findPython()

	cmd := exec.Command(pythonBin, "-m", "devai_ml.server")

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

	client := &StdioClient{
		cmd:    cmd,
		stdin:  stdin,
		stdout: bufio.NewReader(stdout),
	}
	for _, opt := range opts {
		opt(client)
	}

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

// Close stops the Python ML service.
func (c *StdioClient) Close() error {
	c.stdin.Close()
	return c.cmd.Wait()
}
