package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

var hooksCmd = &cobra.Command{
	Use:   "hooks",
	Short: "Manage git hooks for auto-indexing",
}

var hooksInstallCmd = &cobra.Command{
	Use:   "install [repo-path]",
	Short: "Install post-commit hook for auto-indexing",
	Long:  `Installs a git post-commit hook that triggers devai index after each commit.`,
	Args:  cobra.MaximumNArgs(1),
	RunE:  runHooksInstall,
}

var hooksUninstallCmd = &cobra.Command{
	Use:   "uninstall [repo-path]",
	Short: "Remove devai post-commit hook",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runHooksUninstall,
}

func init() {
	hooksCmd.AddCommand(hooksInstallCmd)
	hooksCmd.AddCommand(hooksUninstallCmd)
	rootCmd.AddCommand(hooksCmd)
}

const hookMarker = "# DEVAI_AUTO_INDEX"

// hookScript generates the post-commit hook content.
// It finds the devai binary path and runs index in background.
func hookScript(devaiBinary string, stateDir string) string {
	return fmt.Sprintf(`#!/bin/sh
%s
# Auto-index after commit. Installed by: devai hooks install
# Remove with: devai hooks uninstall

# Run indexing in background so commit isn't blocked
DEVAI_STATE_DIR="%s" "%s" index --incremental &
`, hookMarker, stateDir, devaiBinary)
}

func runHooksInstall(cmd *cobra.Command, args []string) error {
	repoPath := "."
	if len(args) > 0 {
		repoPath = args[0]
	}

	absPath, err := filepath.Abs(repoPath)
	if err != nil {
		return fmt.Errorf("resolving path: %w", err)
	}

	// Verify it's a git repo
	gitDir := filepath.Join(absPath, ".git")
	if info, err := os.Stat(gitDir); err != nil || !info.IsDir() {
		return fmt.Errorf("%s is not a git repository", absPath)
	}

	// Find devai binary
	devaiBinary, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding devai binary: %w", err)
	}
	devaiBinary, _ = filepath.Abs(devaiBinary)

	// Determine state dir
	stateDir := os.Getenv("DEVAI_STATE_DIR")
	if stateDir == "" {
		stateDir = filepath.Join(absPath, ".devai", "state")
	}

	hooksDir := filepath.Join(gitDir, "hooks")
	if err := os.MkdirAll(hooksDir, 0o755); err != nil {
		return fmt.Errorf("creating hooks directory: %w", err)
	}

	hookPath := filepath.Join(hooksDir, "post-commit")

	// Check if hook already exists
	if data, err := os.ReadFile(hookPath); err == nil {
		content := string(data)
		// If our hook is already there, update it
		if strings.Contains(content, hookMarker) {
			fmt.Println("Updating existing devai post-commit hook")
		} else {
			// Append to existing hook
			fmt.Println("Appending devai auto-index to existing post-commit hook")
			script := fmt.Sprintf("\n%s\nDEVAI_STATE_DIR=\"%s\" \"%s\" index --incremental &\n",
				hookMarker, stateDir, devaiBinary)
			f, err := os.OpenFile(hookPath, os.O_APPEND|os.O_WRONLY, 0o755)
			if err != nil {
				return fmt.Errorf("appending to hook: %w", err)
			}
			defer f.Close()
			if _, err := f.WriteString(script); err != nil {
				return fmt.Errorf("writing hook: %w", err)
			}
			fmt.Printf("Hook installed: %s\n", hookPath)
			return nil
		}
	}

	// Write new hook
	script := hookScript(devaiBinary, stateDir)
	if err := os.WriteFile(hookPath, []byte(script), 0o755); err != nil {
		return fmt.Errorf("writing hook: %w", err)
	}

	fmt.Printf("Post-commit hook installed: %s\n", hookPath)
	fmt.Printf("  Binary: %s\n", devaiBinary)
	fmt.Printf("  State:  %s\n", stateDir)
	fmt.Println("\nAuto-indexing will run after each commit (in background).")
	return nil
}

func runHooksUninstall(cmd *cobra.Command, args []string) error {
	repoPath := "."
	if len(args) > 0 {
		repoPath = args[0]
	}

	absPath, err := filepath.Abs(repoPath)
	if err != nil {
		return fmt.Errorf("resolving path: %w", err)
	}

	hookPath := filepath.Join(absPath, ".git", "hooks", "post-commit")

	data, err := os.ReadFile(hookPath)
	if err != nil {
		fmt.Println("No post-commit hook found")
		return nil
	}

	content := string(data)
	if !strings.Contains(content, hookMarker) {
		fmt.Println("No devai hook found in post-commit")
		return nil
	}

	// If the entire hook is ours, remove it
	// Otherwise just remove our lines
	lines := strings.Split(content, "\n")
	var kept []string
	skip := false
	for _, line := range lines {
		if strings.Contains(line, hookMarker) {
			skip = true
			continue
		}
		if skip {
			// Skip the next few lines that are part of our hook
			if strings.Contains(line, "devai") || strings.Contains(line, "DEVAI_STATE_DIR") ||
				strings.Contains(line, "Auto-index") || strings.Contains(line, "Remove with") || line == "" {
				continue
			}
			skip = false
		}
		kept = append(kept, line)
	}

	// If only shebang left (or empty), remove the file
	meaningful := false
	for _, line := range kept {
		if line != "" && line != "#!/bin/sh" && line != "#!/bin/bash" {
			meaningful = true
			break
		}
	}

	if !meaningful {
		os.Remove(hookPath)
		fmt.Println("Post-commit hook removed")
	} else {
		result := strings.Join(kept, "\n") + "\n"
		os.WriteFile(hookPath, []byte(result), 0o755)
		fmt.Println("DevAI hook removed from post-commit (other hooks preserved)")
	}
	return nil
}
