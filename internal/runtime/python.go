package runtime

import (
	"fmt"
	"os"
	"path/filepath"
	goruntime "runtime"

	"github.com/snaven10/devai/internal/config"
)

// FindPython locates the Python binary using a prioritized resolution order.
// It stops at the first valid candidate found.
//
// Resolution order:
//  1. DEVAI_PYTHON env var (explicit override)
//  2. Config file: cfg.Runtime.PythonPath (from .devai/config.yaml)
//  3. Installed location: ~/.local/share/devai/python/venv/bin/python
//  4. Relative to executable: {binary_dir}/../ml/.venv/bin/python
//  5. Relative to cwd: ml/.venv/bin/python
//  6. System fallback: python3 (Linux/macOS) or python (Windows)
func FindPython(cfg *config.ProjectConfig) string {
	// 1. Explicit env var
	if p := os.Getenv("DEVAI_PYTHON"); p != "" {
		fmt.Fprintf(os.Stderr, "[runtime] python: using DEVAI_PYTHON env var: %s\n", p)
		return p
	}

	// 2. Config file
	if cfg != nil && cfg.Runtime.PythonPath != "" {
		p := cfg.Runtime.PythonPath
		if fileExists(p) {
			fmt.Fprintf(os.Stderr, "[runtime] python: using config python_path: %s\n", p)
			return p
		}
	}

	// 3. Installed location (~/.local/share/devai/python/venv)
	if home, err := os.UserHomeDir(); err == nil {
		p := filepath.Join(home, ".local", "share", "devai", "python", "venv", venvBinPython())
		if fileExists(p) {
			fmt.Fprintf(os.Stderr, "[runtime] python: using installed venv: %s\n", p)
			return p
		}
	}

	// 4. Relative to executable
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		p := filepath.Join(exeDir, "..", "ml", ".venv", venvBinPython())
		if fileExists(p) {
			fmt.Fprintf(os.Stderr, "[runtime] python: using exe-relative venv: %s\n", p)
			return p
		}
	}

	// 5. Relative to cwd
	if cwd, err := os.Getwd(); err == nil {
		p := filepath.Join(cwd, "ml", ".venv", venvBinPython())
		if fileExists(p) {
			fmt.Fprintf(os.Stderr, "[runtime] python: using cwd-relative venv: %s\n", p)
			return p
		}
	}

	// 6. System fallback
	fallback := systemPython()
	fmt.Fprintf(os.Stderr, "[runtime] python: using system fallback: %s\n", fallback)
	return fallback
}

// venvBinPython returns the relative path inside a venv to the python binary,
// accounting for Windows vs Unix layout.
func venvBinPython() string {
	if goruntime.GOOS == "windows" {
		return filepath.Join("Scripts", "python.exe")
	}
	return filepath.Join("bin", "python")
}

// systemPython returns the system python command name for the current OS.
func systemPython() string {
	if goruntime.GOOS == "windows" {
		return "python"
	}
	return "python3"
}

// fileExists checks whether the given path exists and is not a directory.
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
