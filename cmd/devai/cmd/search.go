package cmd

import (
	"encoding/json"
	"fmt"
	"strings"

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
	searchCmd.Flags().String("language", "", "Filter by language")
	searchCmd.Flags().String("format", "table", "Output format: table, json")
	rootCmd.AddCommand(searchCmd)
}

func runSearch(cmd *cobra.Command, args []string) error {
	query := strings.Join(args, " ")
	limit, _ := cmd.Flags().GetInt("limit")
	branch, _ := cmd.Flags().GetString("branch")
	language, _ := cmd.Flags().GetString("language")
	format, _ := cmd.Flags().GetString("format")

	client, err := mlclient.NewStdioClient()
	if err != nil {
		return fmt.Errorf("connecting to ML service: %w", err)
	}
	defer client.Close()

	params := map[string]interface{}{
		"query": query,
		"limit": limit,
	}
	if branch != "" {
		params["branch"] = branch
	}
	if language != "" {
		params["language"] = language
	}

	result, err := client.Call("search", params)
	if err != nil {
		return fmt.Errorf("search failed: %w", err)
	}

	if format == "json" {
		formatted, _ := json.MarshalIndent(result, "", "  ")
		fmt.Println(string(formatted))
		return nil
	}

	// Table format
	m, ok := result.(map[string]interface{})
	if !ok {
		formatted, _ := json.MarshalIndent(result, "", "  ")
		fmt.Println(string(formatted))
		return nil
	}

	count := 0
	if c, ok := m["count"].(float64); ok {
		count = int(c)
	}
	fmt.Printf("Search: %q — %d results\n\n", query, count)

	results, ok := m["results"].([]interface{})
	if !ok || len(results) == 0 {
		fmt.Println("No results found.")
		return nil
	}

	for i, r := range results {
		item, ok := r.(map[string]interface{})
		if !ok {
			continue
		}
		file, _ := item["file"].(string)
		symbol, _ := item["symbol"].(string)
		symbolType, _ := item["symbol_type"].(string)
		lang, _ := item["language"].(string)
		score, _ := item["score"].(float64)
		startLine, _ := item["start_line"].(float64)
		endLine, _ := item["end_line"].(float64)
		level, _ := item["chunk_level"].(string)
		text, _ := item["text"].(string)

		fmt.Printf("[%d] %s:%g-%g  (score: %.4f)\n", i+1, file, startLine, endLine, score)
		if symbol != "" {
			fmt.Printf("    %s %s (%s) [%s]\n", symbolType, symbol, lang, level)
		}
		// Show first 3 lines of text
		lines := strings.Split(text, "\n")
		preview := lines
		if len(preview) > 4 {
			preview = preview[:4]
		}
		for _, line := range preview {
			fmt.Printf("    │ %s\n", line)
		}
		fmt.Println()
	}

	return nil
}
