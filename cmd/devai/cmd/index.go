package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

// resolvedClientOpts loads project config and returns mlclient options
// with storage env vars, project config, and state dir resolved.
func resolvedClientOpts() ([]mlclient.Option, error) {
	projectCfg, storageEnv, err := resolvedStorageConfig()
	if err != nil {
		return nil, err
	}
	opts := []mlclient.Option{
		mlclient.WithEnv(storageEnv),
		mlclient.WithConfig(projectCfg),
	}
	return opts, nil
}

var indexCmd = &cobra.Command{
	Use:   "index",
	Short: "Index the current repository",
	Long:  `Index the current repository using git-aware incremental indexing.`,
	RunE:  runIndex,
}

func init() {
	indexCmd.Flags().Bool("incremental", true, "Only index changed files since last index")
	indexCmd.Flags().String("branch", "", "Branch to index (default: current)")
	rootCmd.AddCommand(indexCmd)
}

func runIndex(cmd *cobra.Command, args []string) error {
	incremental, _ := cmd.Flags().GetBool("incremental")
	branch, _ := cmd.Flags().GetString("branch")

	opts, err := resolvedClientOpts()
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("connecting to ML service: %w", err)
	}
	defer client.Close()

	params := map[string]interface{}{
		"repo_path":   ".",
		"incremental": incremental,
	}
	if branch != "" {
		params["branch"] = branch
	}

	result, err := client.Call("index_repo", params)
	if err != nil {
		return fmt.Errorf("indexing failed: %w", err)
	}

	// Pretty print result
	formatted, _ := json.MarshalIndent(result, "", "  ")
	fmt.Println(string(formatted))
	return nil
}
