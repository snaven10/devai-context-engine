package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

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

	client, err := mlclient.NewStdioClient()
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
