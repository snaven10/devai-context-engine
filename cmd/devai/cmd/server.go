package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/snaven10/devai/internal/config"
	"github.com/snaven10/devai/internal/mcp"
	"github.com/snaven10/devai/internal/mlclient"
	"github.com/snaven10/devai/internal/runtime"
	"github.com/snaven10/devai/internal/storage"
)

var serverCmd = &cobra.Command{
	Use:   "server",
	Short: "Manage the ML service and MCP server",
}

var serverStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start the ML service",
	RunE:  runServerStart,
}

var serverStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Check ML service status",
	RunE:  runServerStatus,
}

var serverMCPCmd = &cobra.Command{
	Use:   "mcp",
	Short: "Start the MCP server (for AI agent integration)",
	Long:  `Start the MCP server on stdin/stdout for integration with AI agents like Claude Code.`,
	RunE:  runServerMCP,
}

func init() {
	serverStartCmd.Flags().String("model", "minilm-l6", "Embedding model")
	serverStartCmd.Flags().String("state-dir", ".devai/state", "State directory")
	serverStartCmd.Flags().Bool("background", false, "Run in background")

	serverCmd.AddCommand(serverStartCmd)
	serverCmd.AddCommand(serverStatusCmd)
	serverCmd.AddCommand(serverMCPCmd)
	rootCmd.AddCommand(serverCmd)
}

// resolvedStorageConfig loads project config and merges with env var overrides,
// returning the project config and KEY=VALUE pairs ready to propagate to the ML sidecar.
// Uses --config flag if provided, otherwise auto-detects .devai/config.yaml from CWD.
func resolvedStorageConfig() (*config.ProjectConfig, []string, error) {
	var projectCfg config.ProjectConfig
	var err error

	if cfgFile != "" {
		projectCfg, err = config.LoadConfig(cfgFile)
		if err != nil {
			return nil, nil, fmt.Errorf("loading config %s: %w", cfgFile, err)
		}
	} else {
		projectCfg, _ = config.LoadConfigFromCWD()
	}

	router, err := storage.NewFromConfigWithEnvOverride(projectCfg)
	if err != nil {
		return nil, nil, fmt.Errorf("resolving storage config: %w", err)
	}
	return &projectCfg, router.EnvVars(), nil
}

// printSyncResult prints a formatted summary for push/pull/sync operations.
func printSyncResult(op string, result interface{}) {
	m, ok := result.(map[string]interface{})
	if !ok {
		fmt.Printf("%s complete.\n", op)
		return
	}

	repo, _ := m["repo"].(string)
	branch, _ := m["branch"].(string)
	collection, _ := m["collection"].(string)

	fmt.Printf("\n%s complete: %s\n", op, repo)
	fmt.Printf("  Branch:     %s\n", branch)
	fmt.Printf("  Collection: %s\n", collection)

	// Branch breakdown
	if branches, ok := m["branches"].(map[string]interface{}); ok && len(branches) > 0 {
		fmt.Println("  Branches:")
		for b, count := range branches {
			fmt.Printf("    %-40s %v vectors\n", b, count)
		}
	}

	// Operation-specific counts
	if v, ok := m["pushed"]; ok {
		fmt.Printf("  Pushed:     %v\n", v)
	}
	if v, ok := m["pulled"]; ok {
		fmt.Printf("  Pulled:     %v\n", v)
	}
	if v, ok := m["total_local"]; ok {
		fmt.Printf("  Local:      %v vectors\n", v)
	}
	if v, ok := m["total_remote"]; ok {
		fmt.Printf("  Remote:     %v vectors\n", v)
	}
	if v, ok := m["conflicts"]; ok {
		conflicts, _ := v.(float64)
		if conflicts > 0 {
			fmt.Printf("  Conflicts:  %v (resolved: %v)\n", v, m["resolution"])
		}
	}
	if v, ok := m["errors"]; ok {
		errors, _ := v.(float64)
		if errors > 0 {
			fmt.Printf("  Errors:     %v\n", v)
		}
	}
	fmt.Println()
}

func runServerStart(cmd *cobra.Command, args []string) error {
	model, _ := cmd.Flags().GetString("model")
	stateDir, _ := cmd.Flags().GetString("state-dir")
	background, _ := cmd.Flags().GetBool("background")

	projectCfg, storageEnv, err := resolvedStorageConfig()
	if err != nil {
		return err
	}

	// If state-dir was not explicitly set via CLI, check config file
	if !cmd.Flags().Changed("state-dir") && projectCfg != nil && projectCfg.StateDir != "" {
		stateDir = projectCfg.StateDir
	}

	pythonBin := runtime.FindPython(projectCfg)
	mlCmd := exec.Command(pythonBin, "-m", "devai_ml.server",
		"--model", model,
		"--state-dir", stateDir,
	)
	mlCmd.Env = append(os.Environ(), storageEnv...)

	if background {
		mlCmd.Stdout = nil
		mlCmd.Stderr = nil
		if err := mlCmd.Start(); err != nil {
			return fmt.Errorf("starting ML service: %w", err)
		}

		// Write PID file
		pidFile := filepath.Join(stateDir, "ml-service.pid")
		os.MkdirAll(filepath.Dir(pidFile), 0o755)
		os.WriteFile(pidFile, []byte(fmt.Sprintf("%d", mlCmd.Process.Pid)), 0o644)

		fmt.Printf("ML service started (PID: %d)\n", mlCmd.Process.Pid)
		return nil
	}

	// Foreground mode
	mlCmd.Stdout = os.Stdout
	mlCmd.Stderr = os.Stderr
	mlCmd.Stdin = os.Stdin
	return mlCmd.Run()
}

func runServerMCP(cmd *cobra.Command, args []string) error {
	projectCfg, storageEnv, err := resolvedStorageConfig()
	if err != nil {
		return err
	}

	// Start ML service as quiet sidecar (no logs to stderr — MCP uses stderr)
	mcpOpts := []mlclient.Option{
		mlclient.WithQuiet(),
		mlclient.WithEnv(storageEnv),
		mlclient.WithConfig(projectCfg),
	}
	client, err := mlclient.NewStdioClient(mcpOpts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	// Start MCP server on stdin/stdout
	mcpSrv := mcp.New(client)
	return mcpSrv.ServeStdio()
}

func runServerStatus(cmd *cobra.Command, args []string) error {
	pidFile := ".devai/state/ml-service.pid"
	data, err := os.ReadFile(pidFile)
	if err != nil {
		fmt.Println("ML service: not running (no PID file)")
		return nil
	}

	fmt.Printf("ML service: PID file found (%s)\n", string(data))
	return nil
}
