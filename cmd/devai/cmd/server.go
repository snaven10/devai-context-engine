package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/snaven10/devai/internal/mcp"
	"github.com/snaven10/devai/internal/mlclient"
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

func runServerStart(cmd *cobra.Command, args []string) error {
	model, _ := cmd.Flags().GetString("model")
	stateDir, _ := cmd.Flags().GetString("state-dir")
	background, _ := cmd.Flags().GetBool("background")

	mlCmd := exec.Command("python3", "-m", "devai_ml.server",
		"--model", model,
		"--state-dir", stateDir,
	)

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
	// Start ML service as quiet sidecar (no logs to stderr — MCP uses stderr)
	client, err := mlclient.NewStdioClient(mlclient.WithQuiet())
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
