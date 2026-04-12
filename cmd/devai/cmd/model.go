package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/snaven10/devai/internal/config"
	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
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

var modelUseCmd = &cobra.Command{
	Use:   "use <model-key>",
	Short: "Switch the active embedding model",
	Long: `Change the embedding model used for indexing. This updates the
config file and will trigger a full reindex on next 'devai index'.

Available models: minilm-l6, minilm-l12, bge-small, bge-base`,
	Args: cobra.ExactArgs(1),
	RunE: runModelUse,
}

var modelDownloadCmd = &cobra.Command{
	Use:   "download <model-key>",
	Short: "Download a model to local cache without switching",
	Long: `Pre-download an embedding model from HuggingFace Hub so it's
available for offline use later. Does not change the active model.`,
	Args: cobra.ExactArgs(1),
	RunE: runModelDownload,
}

func init() {
	modelCmd.AddCommand(modelListCmd)
	modelCmd.AddCommand(modelUpdateCmd)
	modelCmd.AddCommand(modelInfoCmd)
	modelCmd.AddCommand(modelUseCmd)
	modelCmd.AddCommand(modelDownloadCmd)
	rootCmd.AddCommand(modelCmd)
}

// resolvedLang returns "es" or "en" based on project config.
func resolvedLang() string {
	cfg, _ := config.LoadConfigFromCWD()
	if strings.ToLower(cfg.Language) == "es" {
		return "es"
	}
	return "en"
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

	lang := resolvedLang()
	result, err := client.Call("model_list", map[string]interface{}{"lang": lang})
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

	if lang == "es" {
		fmt.Println("Modelos de embedding disponibles:")
		fmt.Println()
	} else {
		fmt.Println("Available embedding models:")
		fmt.Println()
	}

	for _, model := range models {
		entry, ok := model.(map[string]interface{})
		if !ok {
			continue
		}
		key, _ := entry["key"].(string)
		name, _ := entry["name"].(string)
		dim, _ := entry["dimension"].(float64)
		sizeMB, _ := entry["size_mb"].(float64)
		speed, _ := entry["speed"].(string)
		quality, _ := entry["quality"].(string)
		cached, _ := entry["cached"].(bool)
		desc, _ := entry["description"].(string)

		marker := "  "
		if key == current || name == current {
			marker = "* "
		}
		cacheTag := ""
		if cached {
			cacheTag = " [cached]"
		}
		fmt.Printf("  %s%-12s  %s  dim=%d  ~%dMB  speed=%s  quality=%s%s\n",
			marker, key, name, int(dim), int(sizeMB), speed, quality, cacheTag)
		if desc != "" {
			fmt.Printf("    %s\n\n", desc)
		}
	}

	if lang == "es" {
		fmt.Println("  * = modelo actual")
		fmt.Println("\n  Uso: devai model use <key>       Cambiar modelo")
		fmt.Println("       devai model download <key>  Descargar sin cambiar")
	} else {
		fmt.Println("  * = current model")
		fmt.Println("\n  Usage: devai model use <key>       Switch model")
		fmt.Println("         devai model download <key>  Download without switching")
	}
	return nil
}

func runModelUpdate(cmd *cobra.Command, args []string) error {
	lang := resolvedLang()
	if lang == "es" {
		fmt.Println("Actualizando modelo de embedding (consultando HuggingFace Hub)...")
	} else {
		fmt.Println("Updating embedding model (checking HuggingFace Hub)...")
	}

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
			if lang == "es" {
				fmt.Printf("Modelo actualizado exitosamente: %v\n", model)
			} else {
				fmt.Printf("Model updated successfully: %v\n", model)
			}
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

	lang := resolvedLang()
	result, err := client.Call("health", map[string]interface{}{})
	if err != nil {
		return fmt.Errorf("getting model info: %w", err)
	}

	if m, ok := result.(map[string]interface{}); ok {
		if lang == "es" {
			fmt.Println("Modelo actual:")
		} else {
			fmt.Println("Current model:")
		}
		if v, ok := m["model_loaded"]; ok {
			fmt.Printf("  Name:      %v\n", v)
		}
		if v, ok := m["model_dimension"]; ok {
			fmt.Printf("  Dimension: %v\n", v)
		}
		if v, ok := m["languages_supported"]; ok {
			if langs, ok := v.([]interface{}); ok {
				if lang == "es" {
					fmt.Printf("  Lenguajes: %d soportados\n", len(langs))
				} else {
					fmt.Printf("  Languages: %d supported\n", len(langs))
				}
			}
		}
	}
	return nil
}

func runModelUse(cmd *cobra.Command, args []string) error {
	modelKey := args[0]
	lang := resolvedLang()

	// Validate model key exists by checking with ML service
	opts, err := resolvedClientOpts()
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}

	result, err := client.Call("model_list", map[string]interface{}{"lang": lang})
	client.Close()
	if err != nil {
		return fmt.Errorf("listing models: %w", err)
	}

	m, _ := result.(map[string]interface{})
	models, _ := m["models"].([]interface{})
	found := false
	for _, model := range models {
		entry, ok := model.(map[string]interface{})
		if !ok {
			continue
		}
		if key, _ := entry["key"].(string); key == modelKey {
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("unknown model: %s. Run 'devai model list' to see available models", modelKey)
	}

	// Update the config file
	if err := updateConfigModel(modelKey); err != nil {
		return fmt.Errorf("updating config: %w", err)
	}

	if lang == "es" {
		fmt.Printf("Modelo cambiado a: %s\n", modelKey)
		fmt.Println("Ejecuta 'devai index --incremental=false' para re-indexar con el nuevo modelo.")
	} else {
		fmt.Printf("Model switched to: %s\n", modelKey)
		fmt.Println("Run 'devai index --incremental=false' to reindex with the new model.")
	}
	return nil
}

func runModelDownload(cmd *cobra.Command, args []string) error {
	modelKey := args[0]
	lang := resolvedLang()

	if lang == "es" {
		fmt.Printf("Descargando modelo %s...\n", modelKey)
	} else {
		fmt.Printf("Downloading model %s...\n", modelKey)
	}

	opts, err := resolvedClientOptsWithOffline(false)
	if err != nil {
		return fmt.Errorf("resolving config: %w", err)
	}
	client, err := mlclient.NewStdioClient(opts...)
	if err != nil {
		return fmt.Errorf("starting ML service: %w", err)
	}
	defer client.Close()

	result, err := client.Call("model_download", map[string]interface{}{"model": modelKey})
	if err != nil {
		return fmt.Errorf("download failed: %w", err)
	}

	if m, ok := result.(map[string]interface{}); ok {
		status, _ := m["status"].(string)
		modelName, _ := m["model"].(string)
		if status == "already_cached" {
			if lang == "es" {
				fmt.Printf("El modelo %s ya esta en cache.\n", modelName)
			} else {
				fmt.Printf("Model %s is already cached.\n", modelName)
			}
		} else {
			if lang == "es" {
				fmt.Printf("Modelo %s descargado exitosamente.\n", modelName)
			} else {
				fmt.Printf("Model %s downloaded successfully.\n", modelName)
			}
		}
	}
	return nil
}

// updateConfigModel modifies the embeddings.model field in .devai/config.yaml.
func updateConfigModel(modelKey string) error {
	cwd, err := os.Getwd()
	if err != nil {
		return err
	}

	cfgPath := config.FindConfigFile(cwd)
	if cfgPath == "" {
		return fmt.Errorf("no .devai/config.yaml found. Run 'devai init' first")
	}

	data, err := os.ReadFile(cfgPath)
	if err != nil {
		return err
	}

	// Parse as generic YAML to preserve comments and structure
	var doc yaml.Node
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return err
	}

	// Walk the YAML tree to find embeddings.model and update it
	updated := updateYAMLField(&doc, []string{"embeddings", "model"}, modelKey)
	if !updated {
		return fmt.Errorf("could not find embeddings.model in config")
	}

	out, err := yaml.Marshal(&doc)
	if err != nil {
		return err
	}

	return os.WriteFile(cfgPath, out, 0o644)
}

// updateYAMLField navigates a yaml.Node tree and sets a leaf value.
func updateYAMLField(node *yaml.Node, path []string, value string) bool {
	if node.Kind == yaml.DocumentNode && len(node.Content) > 0 {
		return updateYAMLField(node.Content[0], path, value)
	}
	if node.Kind != yaml.MappingNode || len(path) == 0 {
		return false
	}

	for i := 0; i < len(node.Content)-1; i += 2 {
		keyNode := node.Content[i]
		valNode := node.Content[i+1]
		if keyNode.Value == path[0] {
			if len(path) == 1 {
				valNode.Value = value
				return true
			}
			return updateYAMLField(valNode, path[1:], value)
		}
	}
	return false
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
	storageEnv = append(storageEnv, "DEVAI_EMBEDDINGS_OFFLINE="+offlineStr)

	opts := []mlclient.Option{
		mlclient.WithEnv(storageEnv),
		mlclient.WithConfig(projectCfg),
	}
	return opts, nil
}

