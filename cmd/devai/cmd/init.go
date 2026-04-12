package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

var initCmd = &cobra.Command{
	Use:   "init [repo-path]",
	Short: "Initialize devai tracking for a repository",
	Long:  `Initialize devai for a repository. Creates .devai/ directory with configuration and state.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runInit,
}

func init() {
	initCmd.Flags().String("name", "", "Human-friendly repository alias")
	initCmd.Flags().Bool("shared", false, "Enable shared mode (requires API token)")
	rootCmd.AddCommand(initCmd)
}

func runInit(cmd *cobra.Command, args []string) error {
	repoPath := "."
	if len(args) > 0 {
		repoPath = args[0]
	}

	absPath, err := filepath.Abs(repoPath)
	if err != nil {
		return fmt.Errorf("resolving path: %w", err)
	}

	// Check for .git directory
	gitDir := filepath.Join(absPath, ".git")
	if _, err := os.Stat(gitDir); os.IsNotExist(err) {
		return fmt.Errorf("%s is not a git repository (no .git directory)", absPath)
	}

	// Create .devai directory
	devaiDir := filepath.Join(absPath, ".devai")
	stateDir := filepath.Join(devaiDir, "state")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return fmt.Errorf("creating .devai directory: %w", err)
	}

	// Create default config
	configPath := filepath.Join(devaiDir, "config.yaml")
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		name, _ := cmd.Flags().GetString("name")
		if name == "" {
			name = filepath.Base(absPath)
		}
		config := fmt.Sprintf(`# DevAI project configuration
project:
  name: %s
  path: %s

state_dir: %s

embeddings:
  provider: local
  model: minilm-l6
  # offline: auto  # auto=use cache when available, true=always offline, false=always check HF Hub

storage:
  mode: local
  # qdrant_url: localhost:6334
  # qdrant_api_key: ""

indexing:
  exclude:
    - "node_modules/**"
    - "vendor/**"
    - ".git/**"
    - "__pycache__/**"
    - "dist/**"
    - "build/**"
    - "*.min.js"
    - "*.lock"
`, name, absPath, stateDir)

		if err := os.WriteFile(configPath, []byte(config), 0o644); err != nil {
			return fmt.Errorf("writing config: %w", err)
		}
	}

	fmt.Printf("Initialized devai for %s\n", absPath)
	fmt.Printf("  Config: %s\n", configPath)
	fmt.Printf("  State:  %s\n", stateDir)
	return nil
}
