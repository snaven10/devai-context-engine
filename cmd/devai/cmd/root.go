package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	cfgFile string
	verbose bool
	version = "dev"
	commit  = "none"
)

// SetVersionInfo is called from main to inject build-time version info.
func SetVersionInfo(v, c string) {
	version = v
	commit = c
}

var rootCmd = &cobra.Command{
	Use:   "devai",
	Short: "Git-aware AI code intelligence tool",
	Long: `DevAI indexes repositories, builds semantic knowledge, and exposes
tools for AI agents. It supports multiple repositories, git branch awareness,
shared/local storage modes, and multi-language parsing.`,
}

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Print version information",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Printf("devai %s (commit: %s)\n", version, commit)
	},
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default: ~/.config/devai/config.yaml)")
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "verbose output")
	rootCmd.AddCommand(versionCmd)
}
