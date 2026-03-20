package cmd

import (
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var pullIndexCmd = &cobra.Command{
	Use:   "pull-index",
	Short: "Pull shared index from Qdrant to local storage",
	Long: `Download a vector index from the shared Qdrant store so you don't need
to re-index locally. Useful for onboarding or syncing with team changes.

Requires DEVAI_STORAGE_MODE=shared or hybrid and DEVAI_QDRANT_URL to be set.`,
	RunE: runPullIndex,
}

func init() {
	pullIndexCmd.Flags().String("repo", "", "Repository path or identifier (required)")
	pullIndexCmd.Flags().String("branch", "", "Branch to pull (default: all branches)")
	_ = pullIndexCmd.MarkFlagRequired("repo")
	rootCmd.AddCommand(pullIndexCmd)
}

func runPullIndex(cmd *cobra.Command, args []string) error {
	repo, _ := cmd.Flags().GetString("repo")
	branch, _ := cmd.Flags().GetString("branch")

	projectCfg, storageEnv, err := resolvedStorageConfig()
	if err != nil {
		return err
	}
	client, err := mlclient.NewStdioClient(mlclient.WithEnv(storageEnv), mlclient.WithConfig(projectCfg))
	if err != nil {
		return fmt.Errorf("connecting to ML service: %w", err)
	}
	defer client.Close()

	result, err := client.PullIndex(repo, branch)
	if err != nil {
		return fmt.Errorf("pull-index failed: %w", err)
	}

	printSyncResult("Pull", result)
	return nil
}
