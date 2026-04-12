package config

import (
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

// RuntimeConfig holds settings for the ML runtime environment.
type RuntimeConfig struct {
	PythonPath string `yaml:"python_path"` // explicit path to python binary
}

// ProjectConfig mirrors the .devai/config.yaml structure.
type ProjectConfig struct {
	Project struct {
		Name string `yaml:"name"`
		Path string `yaml:"path"`
	} `yaml:"project"`
	StateDir string `yaml:"state_dir"`
	Language string `yaml:"language"` // "en" (default) or "es"
	Embeddings struct {
		Provider string `yaml:"provider"`
		Model    string `yaml:"model"`
		Offline  string `yaml:"offline"` // "auto" (default), "true", or "false"
	} `yaml:"embeddings"`
	Storage struct {
		Mode        string `yaml:"mode"`
		QdrantURL   string `yaml:"qdrant_url"`
		QdrantKey   string `yaml:"qdrant_api_key"`
		LocalDBPath string `yaml:"local_db_path"`
	} `yaml:"storage"`
	Indexing struct {
		Exclude []string `yaml:"exclude"`
	} `yaml:"indexing"`
	Runtime RuntimeConfig `yaml:"runtime"`
}

// ConfigFileName is the expected config file name inside the .devai directory.
const ConfigFileName = ".devai/config.yaml"

// FindConfigFile walks up from startDir looking for .devai/config.yaml.
// Returns the absolute path if found, or empty string if not.
func FindConfigFile(startDir string) string {
	dir, err := filepath.Abs(startDir)
	if err != nil {
		return ""
	}

	for {
		candidate := filepath.Join(dir, ConfigFileName)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}

		parent := filepath.Dir(dir)
		if parent == dir {
			// Reached filesystem root.
			return ""
		}
		dir = parent
	}
}

// LoadConfig reads and parses a config YAML file at the given path.
// Returns an error only if the file exists but cannot be read or parsed.
func LoadConfig(path string) (ProjectConfig, error) {
	var cfg ProjectConfig

	data, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}

	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return cfg, err
	}

	return cfg, nil
}

// LoadConfigFromCWD is a convenience function that finds .devai/config.yaml
// by walking up from the current working directory, then loads it.
// Returns a zero-value ProjectConfig (not an error) if no config file is found.
func LoadConfigFromCWD() (ProjectConfig, error) {
	cwd, err := os.Getwd()
	if err != nil {
		return ProjectConfig{}, nil
	}

	path := FindConfigFile(cwd)
	if path == "" {
		return ProjectConfig{}, nil
	}

	return LoadConfig(path)
}
