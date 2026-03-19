package cmd

import (
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show repository and index status",
	RunE:  runStatus,
}

func init() {
	rootCmd.AddCommand(statusCmd)
}

func runStatus(cmd *cobra.Command, args []string) error {
	client, err := mlclient.NewStdioClient()
	if err != nil {
		fmt.Println("ML Service: not running")
		return nil
	}
	defer client.Close()

	result, err := client.Call("health", map[string]interface{}{})
	if err != nil {
		fmt.Println("ML Service: error -", err)
		return nil
	}

	fmt.Println("ML Service: running")
	if m, ok := result.(map[string]interface{}); ok {
		if model, ok := m["model_loaded"]; ok {
			fmt.Printf("  Model: %v\n", model)
		}
		if dim, ok := m["model_dimension"]; ok {
			fmt.Printf("  Dimension: %v\n", dim)
		}
		if langs, ok := m["languages_supported"]; ok {
			fmt.Printf("  Languages: %v\n", langs)
		}
	}
	return nil
}
