package cmd

import (
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var syncIndexCmd = &cobra.Command{
	Use:   "sync-index",
	Short: "Bidirectional sync between local and shared index",
	Long: `Reconcile the local LanceDB index with the shared Qdrant index.

Points only in local are pushed to shared. Points only in shared are pulled
to local. Conflicts (same ID, different content) are resolved using
last-write-wins based on indexed_at timestamps.

This operation is additive — no points are deleted from either store.

Requires DEVAI_STORAGE_MODE=shared or hybrid and DEVAI_QDRANT_URL to be set.`,
	RunE: runSyncIndex,
}

func init() {
	syncIndexCmd.Flags().String("repo", "", "Repository path or identifier (required)")
	syncIndexCmd.Flags().String("branch", "", "Branch to sync (default: all branches)")
	_ = syncIndexCmd.MarkFlagRequired("repo")
	rootCmd.AddCommand(syncIndexCmd)
}

func runSyncIndex(cmd *cobra.Command, args []string) error {
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

	result, err := client.SyncIndex(repo, branch)
	if err != nil {
		return fmt.Errorf("sync-index failed: %w", err)
	}

	printSyncResult("Sync", result)
	return nil
}
