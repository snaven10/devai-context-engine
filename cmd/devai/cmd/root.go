package cmd

import (
	"github.com/spf13/cobra"
)

var (
	cfgFile string
	verbose bool
)

var rootCmd = &cobra.Command{
	Use:   "devai",
	Short: "Git-aware AI code intelligence tool",
	Long: `DevAI indexes repositories, builds semantic knowledge, and exposes
tools for AI agents. It supports multiple repositories, git branch awareness,
shared/local storage modes, and multi-language parsing.`,
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default: ~/.config/devai/config.yaml)")
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "verbose output")
}
