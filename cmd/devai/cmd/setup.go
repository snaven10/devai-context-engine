package cmd

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/snaven10/devai/internal/config"
	devairuntime "github.com/snaven10/devai/internal/runtime"
)

const (
	pythonStandaloneRepo = "astral-sh/python-build-standalone"
	pythonVersion        = "3.12"
	devaiRepo            = "snaven10/devai-context-engine"
	httpTimeout          = 120 * time.Second
)

var (
	setupGPU        bool
	setupPythonOnly bool
	setupDepsOnly   bool
	setupStatus     bool
	setupClean      bool
)

var setupCmd = &cobra.Command{
	Use:   "setup",
	Short: "Set up Python environment for DevAI ML services",
	Long: `Download portable Python, create a virtual environment, and install
ML dependencies. This is required when DevAI was installed without Python
(e.g., via go install or manual binary download).

Examples:
  devai setup              # Full setup: Python + venv + deps
  devai setup --gpu        # Use CUDA PyTorch instead of CPU-only
  devai setup --status     # Show current setup status
  devai setup --clean      # Remove venv and recreate
  devai setup --python-only  # Only download portable Python
  devai setup --deps-only    # Only install deps (assumes Python exists)`,
	RunE: runSetup,
}

func init() {
	setupCmd.Flags().BoolVar(&setupGPU, "gpu", false, "Use CUDA PyTorch instead of CPU-only")
	setupCmd.Flags().BoolVar(&setupPythonOnly, "python-only", false, "Only download portable Python, skip venv and deps")
	setupCmd.Flags().BoolVar(&setupDepsOnly, "deps-only", false, "Only install Python deps (assumes Python exists)")
	setupCmd.Flags().BoolVar(&setupStatus, "status", false, "Show current setup status")
	setupCmd.Flags().BoolVar(&setupClean, "clean", false, "Remove existing venv and recreate")
	rootCmd.AddCommand(setupCmd)
}

// dataDir returns the platform-appropriate data directory for DevAI.
func dataDir() (string, error) {
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData == "" {
			return "", fmt.Errorf("LOCALAPPDATA environment variable not set")
		}
		return filepath.Join(localAppData, "devai"), nil
	default:
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("determining home directory: %w", err)
		}
		return filepath.Join(home, ".local", "share", "devai"), nil
	}
}

// pythonDir returns the path to the portable Python installation.
func pythonDir() (string, error) {
	base, err := dataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "python"), nil
}

// venvDir returns the path to the Python virtual environment.
func venvDir() (string, error) {
	pyDir, err := pythonDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(pyDir, "venv"), nil
}

// venvPython returns the path to the python binary inside the venv.
func venvPython() (string, error) {
	vDir, err := venvDir()
	if err != nil {
		return "", err
	}
	if runtime.GOOS == "windows" {
		return filepath.Join(vDir, "Scripts", "python.exe"), nil
	}
	return filepath.Join(vDir, "bin", "python"), nil
}

// venvPip returns the path to the pip binary inside the venv.
func venvPip() (string, error) {
	vDir, err := venvDir()
	if err != nil {
		return "", err
	}
	if runtime.GOOS == "windows" {
		return filepath.Join(vDir, "Scripts", "pip.exe"), nil
	}
	return filepath.Join(vDir, "bin", "pip"), nil
}

// standalonePythonBin returns the path to the standalone python binary.
func standalonePythonBin() (string, error) {
	pyDir, err := pythonDir()
	if err != nil {
		return "", err
	}
	if runtime.GOOS == "windows" {
		return filepath.Join(pyDir, "python.exe"), nil
	}
	return filepath.Join(pyDir, "bin", "python3"), nil
}

func runSetup(cmd *cobra.Command, args []string) error {
	if setupStatus {
		return runSetupStatus()
	}

	if setupDepsOnly {
		return runInstallDeps()
	}

	if setupPythonOnly {
		return runInstallPython()
	}

	// Full setup
	if setupClean {
		if err := runCleanVenv(); err != nil {
			return err
		}
	}

	fmt.Println("DevAI Setup")
	fmt.Println()

	// Step 1: Check/install Python
	pythonBin, err := ensurePython()
	if err != nil {
		return fmt.Errorf("python setup failed: %w", err)
	}

	// Step 2: Create venv
	if err := ensureVenv(pythonBin); err != nil {
		return fmt.Errorf("venv creation failed: %w", err)
	}

	// Step 3: Install deps
	if err := installDeps(); err != nil {
		return fmt.Errorf("dependency installation failed: %w", err)
	}

	// Step 4: Verify
	if err := verifyInstallation(); err != nil {
		fmt.Printf("\n  [WARN] Verification failed: %v\n", err)
		fmt.Println("  Some modules may not have installed correctly. Try running: devai setup --deps-only")
	} else {
		fmt.Println("\n  [OK] All key modules verified successfully.")
	}

	fmt.Println("\nSetup complete. Run 'devai setup --status' to see details.")
	return nil
}

// ── Status ──────────────────────────────────────────────────────────────────

func runSetupStatus() error {
	fmt.Println("DevAI Setup Status:")

	// Go binary
	exe, err := os.Executable()
	if err != nil {
		exe = "unknown"
	}
	fmt.Printf("  Go binary:       %s\n", exe)

	// Python
	cfg, _ := config.LoadConfigFromCWD()
	pythonBin := devairuntime.FindPython(&cfg)

	standaloneBin, _ := standalonePythonBin()
	pythonSource := "system"
	if standaloneBin != "" {
		if _, err := os.Stat(standaloneBin); err == nil {
			if pythonBin == standaloneBin || strings.Contains(pythonBin, "devai/python") {
				pythonSource = "standalone"
			}
		}
	}

	pyVersion := getPythonVersion(pythonBin)
	if pyVersion != "" {
		fmt.Printf("  Python:          %s (%s) — %s\n", pythonBin, pythonSource, pyVersion)
	} else {
		fmt.Printf("  Python:          not found\n")
	}

	// Venv
	vDir, _ := venvDir()
	venvPy, _ := venvPython()
	if vDir != "" {
		if _, err := os.Stat(venvPy); err == nil {
			fmt.Printf("  Venv:            %s (exists)\n", vDir)
		} else {
			fmt.Printf("  Venv:            %s (not created)\n", vDir)
		}
	}

	// Dependencies
	if venvPy != "" {
		if _, err := os.Stat(venvPy); err == nil {
			deps := checkDependencies(venvPy)
			if deps != "" {
				fmt.Printf("  Dependencies:    installed (%s)\n", deps)
			} else {
				fmt.Printf("  Dependencies:    not installed or import errors\n")
			}
		}
	}

	// Storage mode
	if cfg.Storage.Mode != "" {
		storageInfo := cfg.Storage.Mode
		if cfg.Storage.QdrantURL != "" {
			storageInfo += fmt.Sprintf(" (Qdrant: %s)", cfg.Storage.QdrantURL)
		}
		fmt.Printf("  Storage mode:    %s\n", storageInfo)
	}

	// Model
	if cfg.Embeddings.Model != "" {
		fmt.Printf("  Model:           %s\n", cfg.Embeddings.Model)
	}

	return nil
}

func getPythonVersion(pythonBin string) string {
	cmd := exec.Command(pythonBin, "--version")
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func checkDependencies(pythonBin string) string {
	script := `
import sys
parts = []
try:
    import torch; parts.append(f"torch={torch.__version__}")
except: pass
try:
    import sentence_transformers; parts.append(f"sentence-transformers={sentence_transformers.__version__}")
except: pass
try:
    import lancedb; parts.append(f"lancedb={lancedb.__version__}")
except: pass
if parts:
    print(", ".join(parts))
`
	cmd := exec.Command(pythonBin, "-c", script)
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// ── Clean ───────────────────────────────────────────────────────────────────

func runCleanVenv() error {
	vDir, err := venvDir()
	if err != nil {
		return err
	}

	if _, err := os.Stat(vDir); os.IsNotExist(err) {
		fmt.Println("  [INFO] No existing venv to clean.")
		return nil
	}

	fmt.Printf("  [INFO] Removing existing venv at %s...\n", vDir)
	if err := os.RemoveAll(vDir); err != nil {
		return fmt.Errorf("removing venv: %w", err)
	}
	fmt.Println("  [OK] Venv removed.")
	return nil
}

// ── Python Installation ─────────────────────────────────────────────────────

func runInstallPython() error {
	fmt.Println("DevAI Setup — Python Only")
	fmt.Println()
	_, err := ensurePython()
	return err
}

func ensurePython() (string, error) {
	// Check if standalone Python already exists
	standaloneBin, err := standalonePythonBin()
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(standaloneBin); err == nil {
		ver := getPythonVersion(standaloneBin)
		if strings.Contains(ver, pythonVersion) {
			fmt.Printf("  [OK] Portable Python %s already installed — skipping.\n", pythonVersion)
			return standaloneBin, nil
		}
	}

	// Check if system Python 3.12 is available via FindPython
	cfg, _ := config.LoadConfigFromCWD()
	sysPython := devairuntime.FindPython(&cfg)
	if sysPython != "" {
		ver := getPythonVersion(sysPython)
		if strings.Contains(ver, pythonVersion) {
			fmt.Printf("  [OK] System Python %s found at %s — skipping download.\n", ver, sysPython)
			return sysPython, nil
		}
	}

	// Download portable Python
	fmt.Printf("  [INFO] Downloading portable Python %s...\n", pythonVersion)
	return downloadPortablePython()
}

func downloadPortablePython() (string, error) {
	osName, archName, err := pythonPlatformTriple()
	if err != nil {
		return "", err
	}

	// Fetch latest release to find the matching asset URL
	assetURL, err := findPythonAssetURL(osName, archName)
	if err != nil {
		return "", fmt.Errorf("finding Python download URL: %w", err)
	}

	pyDir, err := pythonDir()
	if err != nil {
		return "", err
	}

	// Download to temp file
	fmt.Printf("  [INFO] Downloading from %s\n", assetURL)
	tmpFile, err := os.CreateTemp("", "devai-python-*.tar.gz")
	if err != nil {
		return "", fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()
	defer os.Remove(tmpPath)

	if err := downloadWithProgress(assetURL, tmpFile); err != nil {
		tmpFile.Close()
		return "", fmt.Errorf("downloading Python: %w", err)
	}
	tmpFile.Close()

	// Extract — python-build-standalone tarballs contain a "python/" top-level directory
	fmt.Println("  [INFO] Extracting...")
	if err := os.MkdirAll(pyDir, 0o755); err != nil {
		return "", fmt.Errorf("creating python directory: %w", err)
	}

	if err := extractTarGz(tmpPath, pyDir, 1); err != nil {
		return "", fmt.Errorf("extracting Python: %w", err)
	}

	standaloneBin, err := standalonePythonBin()
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(standaloneBin); os.IsNotExist(err) {
		return "", fmt.Errorf("extraction succeeded but %s not found — unexpected archive layout", standaloneBin)
	}

	fmt.Printf("  [OK] Portable Python installed to %s\n", pyDir)
	return standaloneBin, nil
}

func pythonPlatformTriple() (osName string, archName string, err error) {
	switch runtime.GOOS {
	case "linux":
		osName = "unknown-linux-gnu"
	case "darwin":
		osName = "apple-darwin"
	default:
		return "", "", fmt.Errorf("unsupported OS: %s (only linux and darwin are supported)", runtime.GOOS)
	}

	switch runtime.GOARCH {
	case "amd64":
		archName = "x86_64"
	case "arm64":
		archName = "aarch64"
	default:
		return "", "", fmt.Errorf("unsupported architecture: %s", runtime.GOARCH)
	}

	return osName, archName, nil
}

// githubRelease represents the minimal structure we need from the GitHub API.
type githubRelease struct {
	Assets []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
	} `json:"assets"`
}

func findPythonAssetURL(osName, archName string) (string, error) {
	apiURL := fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", pythonStandaloneRepo)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get(apiURL)
	if err != nil {
		return "", fmt.Errorf("fetching release info: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("GitHub API returned status %d", resp.StatusCode)
	}

	var release githubRelease
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return "", fmt.Errorf("parsing release JSON: %w", err)
	}

	triple := fmt.Sprintf("%s-%s", archName, osName)
	prefix := fmt.Sprintf("cpython-%s", pythonVersion)

	// Prefer install_only_stripped, fallback to install_only
	var strippedURL, normalURL string
	for _, asset := range release.Assets {
		if !strings.Contains(asset.Name, prefix) {
			continue
		}
		if !strings.Contains(asset.Name, triple) {
			continue
		}
		if strings.Contains(asset.Name, "debug") {
			continue
		}

		if strings.Contains(asset.Name, "install_only_stripped") {
			strippedURL = asset.BrowserDownloadURL
		} else if strings.Contains(asset.Name, "install_only") {
			normalURL = asset.BrowserDownloadURL
		}
	}

	if strippedURL != "" {
		return strippedURL, nil
	}
	if normalURL != "" {
		return normalURL, nil
	}

	return "", fmt.Errorf("no Python %s build found for %s", pythonVersion, triple)
}

// ── Venv ────────────────────────────────────────────────────────────────────

func ensureVenv(pythonBin string) error {
	vDir, err := venvDir()
	if err != nil {
		return err
	}

	vPy, err := venvPython()
	if err != nil {
		return err
	}

	if _, err := os.Stat(vPy); err == nil {
		fmt.Printf("  [OK] Virtual environment already exists at %s — skipping.\n", vDir)
		return nil
	}

	fmt.Printf("  [INFO] Creating virtual environment at %s...\n", vDir)
	cmd := exec.Command(pythonBin, "-m", "venv", vDir)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("creating venv: %w", err)
	}

	fmt.Printf("  [OK] Virtual environment created at %s\n", vDir)
	return nil
}

// ── Dependency Installation ─────────────────────────────────────────────────

func runInstallDeps() error {
	fmt.Println("DevAI Setup — Dependencies Only")
	fmt.Println()
	return installDeps()
}

func installDeps() error {
	vPip, err := venvPip()
	if err != nil {
		return err
	}

	vPy, err := venvPython()
	if err != nil {
		return err
	}

	if _, err := os.Stat(vPy); os.IsNotExist(err) {
		return fmt.Errorf("venv not found at %s — run 'devai setup' first to create it", vPy)
	}

	// Upgrade pip first
	fmt.Println("  [INFO] Upgrading pip...")
	pipUpgrade := exec.Command(vPy, "-m", "pip", "install", "--upgrade", "pip", "--quiet")
	pipUpgrade.Stdout = os.Stdout
	pipUpgrade.Stderr = os.Stderr
	_ = pipUpgrade.Run() // non-fatal

	// Determine requirements file
	reqFile := "requirements-cpu.txt"
	if setupGPU {
		reqFile = "requirements-gpu.txt"
		fmt.Println("  [INFO] Installing with GPU (CUDA) PyTorch support")
	} else {
		fmt.Println("  [INFO] Installing with CPU-only PyTorch (use --gpu for CUDA)")
	}

	reqPath, err := findRequirementsFile(reqFile)
	if err != nil {
		return err
	}

	fmt.Printf("  [INFO] Installing dependencies from %s...\n", reqPath)
	installCmd := exec.Command(vPip, "install", "-r", reqPath)
	installCmd.Stdout = os.Stdout
	installCmd.Stderr = os.Stderr

	if err := installCmd.Run(); err != nil {
		// Clean up temp file if we downloaded it
		if strings.HasPrefix(reqPath, os.TempDir()) {
			os.Remove(reqPath)
		}
		return fmt.Errorf("installing dependencies: %w", err)
	}

	// Clean up temp file if we downloaded it
	if strings.HasPrefix(reqPath, os.TempDir()) {
		os.Remove(reqPath)
	}

	fmt.Println("  [OK] Python dependencies installed.")
	return nil
}

// findRequirementsFile looks for the requirements file in multiple locations:
// 1. Next to the devai binary
// 2. In the scripts/ directory relative to the binary
// 3. Download from GitHub release assets
func findRequirementsFile(reqFile string) (string, error) {
	// 1. Next to the binary
	exe, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exe)
		candidate := filepath.Join(exeDir, reqFile)
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}

		// 2. In scripts/ relative to the binary (dev layout)
		candidate = filepath.Join(exeDir, "..", "scripts", reqFile)
		if _, err := os.Stat(candidate); err == nil {
			abs, _ := filepath.Abs(candidate)
			return abs, nil
		}
	}

	// 3. In scripts/ relative to cwd (development)
	if cwd, err := os.Getwd(); err == nil {
		candidate := filepath.Join(cwd, "scripts", reqFile)
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}

	// 4. Download from GitHub release
	fmt.Println("  [INFO] Requirements file not found locally — downloading from GitHub...")
	return downloadRequirementsFile(reqFile)
}

func downloadRequirementsFile(reqFile string) (string, error) {
	// Try latest release first
	url := fmt.Sprintf("https://github.com/%s/releases/latest/download/%s", devaiRepo, reqFile)

	client := &http.Client{
		Timeout: 30 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 10 {
				return fmt.Errorf("too many redirects")
			}
			return nil
		},
	}

	resp, err := client.Get(url)
	if err != nil {
		return "", fmt.Errorf("downloading %s: %w", reqFile, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("could not find %s — not found locally or in GitHub releases (HTTP %d)", reqFile, resp.StatusCode)
	}

	tmpFile, err := os.CreateTemp("", "devai-req-*.txt")
	if err != nil {
		return "", fmt.Errorf("creating temp file: %w", err)
	}

	if _, err := io.Copy(tmpFile, resp.Body); err != nil {
		tmpFile.Close()
		os.Remove(tmpFile.Name())
		return "", fmt.Errorf("writing requirements file: %w", err)
	}
	tmpFile.Close()

	return tmpFile.Name(), nil
}

// ── Verification ────────────────────────────────────────────────────────────

func verifyInstallation() error {
	vPy, err := venvPython()
	if err != nil {
		return err
	}

	fmt.Println("\n  [INFO] Verifying installation...")

	modules := []string{"torch", "sentence_transformers", "lancedb"}
	var failures []string

	for _, mod := range modules {
		cmd := exec.Command(vPy, "-c", fmt.Sprintf("import %s", mod))
		if err := cmd.Run(); err != nil {
			failures = append(failures, mod)
		}
	}

	if len(failures) > 0 {
		return fmt.Errorf("failed to import: %s", strings.Join(failures, ", "))
	}

	return nil
}

// ── Download with Progress ──────────────────────────────────────────────────

type progressWriter struct {
	total      int64
	downloaded int64
	lastPct    int
}

func (pw *progressWriter) Write(p []byte) (int, error) {
	n := len(p)
	pw.downloaded += int64(n)

	if pw.total > 0 {
		pct := int(pw.downloaded * 100 / pw.total)
		if pct != pw.lastPct && pct%5 == 0 {
			pw.lastPct = pct
			fmt.Printf("\r  [INFO] Downloading... %d%% (%s / %s)",
				pct, formatBytes(pw.downloaded), formatBytes(pw.total))
		}
	} else {
		// Unknown total — show bytes downloaded
		if pw.downloaded%(1024*256) < int64(n) {
			fmt.Printf("\r  [INFO] Downloading... %s", formatBytes(pw.downloaded))
		}
	}

	return n, nil
}

func formatBytes(b int64) string {
	const unit = 1024
	if b < unit {
		return fmt.Sprintf("%d B", b)
	}
	div, exp := int64(unit), 0
	for n := b / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(b)/float64(div), "KMGTPE"[exp])
}

func downloadWithProgress(url string, dest *os.File) error {
	client := &http.Client{
		Timeout: httpTimeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 10 {
				return fmt.Errorf("too many redirects")
			}
			return nil
		},
	}

	resp, err := client.Get(url)
	if err != nil {
		return fmt.Errorf("HTTP GET failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download failed with HTTP %d", resp.StatusCode)
	}

	pw := &progressWriter{total: resp.ContentLength, lastPct: -1}
	reader := io.TeeReader(resp.Body, pw)

	if _, err := io.Copy(dest, reader); err != nil {
		return fmt.Errorf("writing download data: %w", err)
	}

	fmt.Println() // newline after progress
	return nil
}

// ── Tar Extraction ──────────────────────────────────────────────────────────

// extractTarGz extracts a .tar.gz file to destDir, stripping the given number
// of leading path components (like tar --strip-components).
func extractTarGz(archivePath, destDir string, stripComponents int) error {
	f, err := os.Open(archivePath)
	if err != nil {
		return fmt.Errorf("opening archive: %w", err)
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("creating gzip reader: %w", err)
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("reading tar entry: %w", err)
		}

		// Strip leading path components
		name := header.Name
		if stripComponents > 0 {
			parts := strings.SplitN(name, "/", stripComponents+1)
			if len(parts) <= stripComponents {
				continue // skip entries that would be fully stripped
			}
			name = parts[stripComponents]
			if name == "" {
				continue
			}
		}

		target := filepath.Join(destDir, name)

		// Security: prevent path traversal
		if !strings.HasPrefix(filepath.Clean(target), filepath.Clean(destDir)) {
			return fmt.Errorf("tar entry %q attempts path traversal", header.Name)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, os.FileMode(header.Mode)|0o755); err != nil {
				return fmt.Errorf("creating directory %s: %w", target, err)
			}

		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return fmt.Errorf("creating parent directory for %s: %w", target, err)
			}

			outFile, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, os.FileMode(header.Mode))
			if err != nil {
				return fmt.Errorf("creating file %s: %w", target, err)
			}

			if _, err := io.Copy(outFile, tr); err != nil {
				outFile.Close()
				return fmt.Errorf("writing file %s: %w", target, err)
			}
			outFile.Close()

		case tar.TypeSymlink:
			// Validate symlink target doesn't escape destDir
			linkTarget := header.Linkname
			resolvedLink := filepath.Join(filepath.Dir(target), linkTarget)
			if !strings.HasPrefix(filepath.Clean(resolvedLink), filepath.Clean(destDir)) {
				// Allow relative symlinks within the archive
				if filepath.IsAbs(linkTarget) {
					continue // skip absolute symlinks that escape
				}
			}

			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return fmt.Errorf("creating parent directory for symlink %s: %w", target, err)
			}
			// Remove existing symlink if present (idempotent)
			os.Remove(target)
			if err := os.Symlink(linkTarget, target); err != nil {
				return fmt.Errorf("creating symlink %s -> %s: %w", target, linkTarget, err)
			}

		case tar.TypeLink:
			// Hard link — resolve relative to destDir
			linkTarget := header.Linkname
			if stripComponents > 0 {
				parts := strings.SplitN(linkTarget, "/", stripComponents+1)
				if len(parts) > stripComponents {
					linkTarget = parts[stripComponents]
				}
			}
			resolvedLink := filepath.Join(destDir, linkTarget)

			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return fmt.Errorf("creating parent directory for hard link %s: %w", target, err)
			}
			os.Remove(target)
			if err := os.Link(resolvedLink, target); err != nil {
				return fmt.Errorf("creating hard link %s -> %s: %w", target, resolvedLink, err)
			}
		}
	}

	return nil
}
