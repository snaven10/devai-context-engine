package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var pullIndexCmd = &cobra.Command{
	Use:   "pull-index [repo] [branch]",
	Short: "Pull shared index to local storage",
	Long:  `Download an index from the shared server so you don't need to reindex locally.`,
	Args:  cobra.MaximumNArgs(2),
	RunE:  runPullIndex,
}

func init() {
	pullIndexCmd.Flags().String("server", "", "Shared server URL")
	pullIndexCmd.Flags().String("token", "", "API token")
	rootCmd.AddCommand(pullIndexCmd)
}

func runPullIndex(cmd *cobra.Command, args []string) error {
	repo := "."
	branch := ""
	if len(args) > 0 {
		repo = args[0]
	}
	if len(args) > 1 {
		branch = args[1]
	}

	server, _ := cmd.Flags().GetString("server")
	if server == "" {
		return fmt.Errorf("--server is required for pull-index")
	}

	fmt.Printf("Pulling index for %s/%s from %s...\n", repo, branch, server)
	fmt.Println("(Pull implementation pending — shared store integration)")
	return nil
}
