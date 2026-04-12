package cmd

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/snaven10/devai/internal/mlclient"
	"github.com/snaven10/devai/internal/tui"
	"github.com/spf13/cobra"
)

var tuiCmd = &cobra.Command{
	Use:   "tui",
	Short: "Open interactive terminal UI",
	Long:  `Open the DevAI terminal UI for browsing repos, searching code, and managing indexes.`,
	RunE:  runTUI,
}

func init() {
	rootCmd.AddCommand(tuiCmd)
}

func runTUI(cmd *cobra.Command, args []string) error {
	// Start ML service as sidecar
	opts, err := resolvedClientOpts()
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	model := tui.New(client, "0.1.0")
	p := tea.NewProgram(model, tea.WithAltScreen())

	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "Error running TUI:", err)
		return err
	}

	return nil
}
