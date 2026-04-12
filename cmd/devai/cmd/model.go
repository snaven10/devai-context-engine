package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var modelCmd = &cobra.Command{
	Use:   "model",
	Short: "Manage embedding models",
}

var modelListCmd = &cobra.Command{
	Use:   "list",
	Short: "List available embedding models",
	RunE:  runModelList,
}

var modelUpdateCmd = &cobra.Command{
	Use:   "update",
	Short: "Update the current embedding model from HuggingFace Hub",
	Long: `Force re-download the embedding model from HuggingFace Hub,
even if a cached version exists. Use this to get the latest model version.`,
	RunE: runModelUpdate,
}

var modelInfoCmd = &cobra.Command{
	Use:   "info",
	Short: "Show current model information",
	RunE:  runModelInfo,
}

func init() {
	modelCmd.AddCommand(modelListCmd)
	modelCmd.AddCommand(modelUpdateCmd)
	modelCmd.AddCommand(modelInfoCmd)
	rootCmd.AddCommand(modelCmd)
}

func runModelList(cmd *cobra.Command, args []string) error {
	opts, err := resolvedClientOpts()
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	result, err := client.Call("model_list", map[string]interface{}{})
	if err != nil {
		return fmt.Errorf("listing models: %w", err)
	}

	m, ok := result.(map[string]interface{})
	if !ok {
		formatted, _ := json.MarshalIndent(result, "", "  ")
		fmt.Println(string(formatted))
		return nil
	}

	current, _ := m["current"].(string)
	models, _ := m["models"].([]interface{})

	fmt.Println("Available embedding models:\n")
	for _, model := range models {
		entry, ok := model.(map[string]interface{})
		if !ok {
			continue
		}
		key, _ := entry["key"].(string)
		name, _ := entry["name"].(string)
		dim, _ := entry["dimension"].(float64)
		cached, _ := entry["cached"].(bool)

		marker := "  "
		if key == current || name == current {
			marker = "* "
		}
		cacheStatus := ""
		if cached {
			cacheStatus = " (cached)"
		}
		fmt.Printf("  %s%-12s  %s  dim=%d%s\n", marker, key, name, int(dim), cacheStatus)
	}
	fmt.Printf("\n  * = current model\n")
	return nil
}

func runModelUpdate(cmd *cobra.Command, args []string) error {
	fmt.Println("Updating embedding model (checking HuggingFace Hub)...")

	// Force online mode for this operation
	opts, err := resolvedClientOptsWithOffline(false)
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	result, err := client.Call("health", map[string]interface{}{})
	if err != nil {
		return fmt.Errorf("model update failed: %w", err)
	}

	if m, ok := result.(map[string]interface{}); ok {
		if model, ok := m["model_loaded"]; ok {
			fmt.Printf("Model updated successfully: %v\n", model)
		}
	}
	return nil
}

func runModelInfo(cmd *cobra.Command, args []string) error {
	opts, err := resolvedClientOpts()
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	result, err := client.Call("health", map[string]interface{}{})
	if err != nil {
		return fmt.Errorf("getting model info: %w", err)
	}

	if m, ok := result.(map[string]interface{}); ok {
		fmt.Println("Current model:")
		if v, ok := m["model_loaded"]; ok {
			fmt.Printf("  Name:      %v\n", v)
		}
		if v, ok := m["model_dimension"]; ok {
			fmt.Printf("  Dimension: %v\n", v)
		}
		if v, ok := m["languages_supported"]; ok {
			if langs, ok := v.([]interface{}); ok {
				fmt.Printf("  Languages: %d supported\n", len(langs))
			}
		}
	}
	return nil
}

// resolvedClientOptsWithOffline loads project config with a specific offline setting.
func resolvedClientOptsWithOffline(offline bool) ([]mlclient.Option, error) {
	projectCfg, storageEnv, err := resolvedStorageConfig()
	if err != nil {
		return nil, err
	}

	offlineStr := "false"
	if offline {
		offlineStr = "true"
	}
	// Pass offline override as env var for the ML process
	storageEnv = append(storageEnv, "DEVAI_EMBEDDINGS_OFFLINE="+offlineStr)

	opts := []mlclient.Option{
		mlclient.WithEnv(storageEnv),
		mlclient.WithConfig(projectCfg),
	}
	return opts, nil
}
