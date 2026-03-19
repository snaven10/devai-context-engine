package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var searchCmd = &cobra.Command{
	Use:   "search <query>",
	Short: "Semantic search across indexed code",
	Long:  `Search the indexed codebase using semantic similarity.`,
	Args:  cobra.MinimumNArgs(1),
	RunE:  runSearch,
}

func init() {
	searchCmd.Flags().Int("limit", 10, "Maximum results")
	searchCmd.Flags().String("branch", "", "Search specific branch")
	searchCmd.Flags().String("format", "table", "Output format: table, json")
	rootCmd.AddCommand(searchCmd)
}

func runSearch(cmd *cobra.Command, args []string) error {
	query := args[0]
	limit, _ := cmd.Flags().GetInt("limit")
	branch, _ := cmd.Flags().GetString("branch")
	format, _ := cmd.Flags().GetString("format")

	client, err := mlclient.NewStdioClient()
	if err != nil {
		return fmt.Errorf("connecting to ML service: %w", err)
	}
	defer client.Close()

	// First embed the query
	embedResult, err := client.Call("embed", map[string]interface{}{
		"text": query,
	})
	if err != nil {
		return fmt.Errorf("embedding query: %w", err)
	}

	_ = embedResult
	_ = limit
	_ = branch

	if format == "json" {
		formatted, _ := json.MarshalIndent(embedResult, "", "  ")
		fmt.Println(string(formatted))
	} else {
		fmt.Printf("Search results for: %q\n", query)
		fmt.Println("(Search implementation pending - vector store query from Go)")
	}
	return nil
}
