package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/snaven10/devai/internal/storage"
	"github.com/spf13/cobra"
)

var pushIndexCmd = &cobra.Command{
	Use:   "push-index",
	Short: "Push local index to shared Qdrant store",
	Long: `Upload your local vector index for a repository to the shared Qdrant store
so other developers can pull and use it without re-indexing.

Requires DEVAI_STORAGE_MODE=shared or hybrid and DEVAI_QDRANT_URL to be set.`,
	RunE: runPushIndex,
}

func init() {
	pushIndexCmd.Flags().String("repo", "", "Repository path or identifier (required)")
	pushIndexCmd.Flags().String("branch", "", "Branch to push (default: current git branch)")
	_ = pushIndexCmd.MarkFlagRequired("repo")
	rootCmd.AddCommand(pushIndexCmd)
}

func runPushIndex(cmd *cobra.Command, args []string) error {
	repo, _ := cmd.Flags().GetString("repo")
	branch, _ := cmd.Flags().GetString("branch")

	fmt.Printf("Pushing index for repo=%s branch=%s ...\n", repo, branch)

	client, err := mlclient.NewStdioClient(mlclient.WithEnv(storage.EnvVarsFromEnv()))
	if err != nil {
		return fmt.Errorf("connecting to ML service: %w", err)
	}
	defer client.Close()

	result, err := client.PushIndex(repo, branch)
	if err != nil {
		return fmt.Errorf("push-index failed: %w", err)
	}

	formatted, _ := json.MarshalIndent(result, "", "  ")
	fmt.Println(string(formatted))
	return nil
}
