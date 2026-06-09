package cmd

import (
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
)

var upgradeCmd = &cobra.Command{
	Use:   "upgrade",
	Short: "Upgrade devai to the latest version",
	Long: `Check for and install the latest version of devai from GitHub releases.
Downloads the precompiled binary and replaces the current installation.

Examples:
  devai upgrade              # Upgrade to latest
  devai upgrade --check      # Only check, don't install
  devai upgrade --version v0.1.3-alpha  # Upgrade to specific version`,
	RunE: runUpgrade,
}

var (
	upgradeCheck   bool
	upgradeVersion string
)

func init() {
	upgradeCmd.Flags().BoolVar(&upgradeCheck, "check", false, "Only check for updates, don't install")
	upgradeCmd.Flags().StringVar(&upgradeVersion, "version", "", "Upgrade to a specific version tag")
	rootCmd.AddCommand(upgradeCmd)
}

// releaseInfo holds the minimal GitHub release data we need.
type releaseInfo struct {
	TagName string `json:"tag_name"`
	HTMLURL string `json:"html_url"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
		Size               int64  `json:"size"`
	} `json:"assets"`
}

// CheckLatestVersion queries GitHub for the latest release version.
// Returns the release info, or nil if no release is found or on error.
func CheckLatestVersion() (*releaseInfo, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	url := fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", devaiRepo)

	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GitHub API returned %d", resp.StatusCode)
	}

	var release releaseInfo
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return nil, err
	}

	return &release, nil
}

// IsNewerVersion returns true if remote version is newer than local.
// Simple string comparison on semver-like tags (v0.1.2-alpha < v0.1.3-alpha).
func IsNewerVersion(local, remote string) bool {
	local = strings.TrimPrefix(local, "v")
	remote = strings.TrimPrefix(remote, "v")
	if local == "dev" || local == "" {
		return true // dev builds always consider releases as newer
	}
	return remote > local
}

func runUpgrade(cmd *cobra.Command, args []string) error {
	fmt.Printf("Current version: %s (commit: %s)\n", version, commit)

	var release *releaseInfo
	var err error

	if upgradeVersion != "" {
		// Fetch specific version
		client := &http.Client{Timeout: 10 * time.Second}
		url := fmt.Sprintf("https://api.github.com/repos/%s/releases/tags/%s", devaiRepo, upgradeVersion)
		resp, err := client.Get(url)
		if err != nil {
			return fmt.Errorf("fetching release %s: %w", upgradeVersion, err)
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			return fmt.Errorf("version %s not found (HTTP %d)", upgradeVersion, resp.StatusCode)
		}

		release = &releaseInfo{}
		if err := json.NewDecoder(resp.Body).Decode(release); err != nil {
			return fmt.Errorf("parsing release: %w", err)
		}
	} else {
		release, err = CheckLatestVersion()
		if err != nil {
			return fmt.Errorf("checking for updates: %w", err)
		}
	}

	if release == nil || release.TagName == "" {
		fmt.Println("No releases found on GitHub.")
		return nil
	}

	if !IsNewerVersion(version, release.TagName) && upgradeVersion == "" {
		fmt.Printf("Already up to date (%s)\n", version)
		return nil
	}

	fmt.Printf("New version available: %s\n", release.TagName)

	if upgradeCheck {
		fmt.Printf("  Release: %s\n", release.HTMLURL)
		return nil
	}

	// Find the binary asset for current platform
	osName := runtime.GOOS
	archName := runtime.GOARCH
	assetName := fmt.Sprintf("devai_%s_%s.tar.gz", osName, archName)

	var downloadURL string
	for _, asset := range release.Assets {
		if asset.Name == assetName {
			downloadURL = asset.BrowserDownloadURL
			break
		}
	}

	if downloadURL == "" {
		// No prebuilt binary — try building from source
		fmt.Printf("No prebuilt binary found for %s/%s.\n", osName, archName)
		fmt.Println("Attempting to build from source...")
		return upgradeBuildFromSource(release.TagName)
	}

	return upgradeFromBinary(downloadURL, release.TagName, release)
}

// upgradeBuildFromSource clones/fetches the repo and builds locally.
func upgradeBuildFromSource(tag string) error {
	// Check if we have the source repo locally
	homeDir, _ := os.UserHomeDir()
	srcDir := filepath.Join(homeDir, "devai-context-engine")

	if _, err := os.Stat(filepath.Join(srcDir, "go.mod")); os.IsNotExist(err) {
		// Try to find it
		candidates := []string{
			srcDir,
			filepath.Join(homeDir, "src", "devai-context-engine"),
			filepath.Join(homeDir, "projects", "devai-context-engine"),
		}
		found := false
		for _, c := range candidates {
			if _, err := os.Stat(filepath.Join(c, "go.mod")); err == nil {
				srcDir = c
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("source not found. Clone the repo or publish a binary release:\n  git clone https://github.com/%s %s", devaiRepo, srcDir)
		}
	}

	fmt.Printf("Building from source at %s...\n", srcDir)

	// Fetch and checkout tag
	fetchCmd := exec.Command("git", "-C", srcDir, "fetch", "--tags")
	fetchCmd.Stdout = os.Stdout
	fetchCmd.Stderr = os.Stderr
	_ = fetchCmd.Run() // non-fatal

	checkoutCmd := exec.Command("git", "-C", srcDir, "checkout", tag)
	checkoutCmd.Stdout = os.Stdout
	checkoutCmd.Stderr = os.Stderr
	if err := checkoutCmd.Run(); err != nil {
		// Tag might not exist yet, use main
		fmt.Printf("Tag %s not found, building from current source...\n", tag)
	}

	// Build with version injection
	installDir, _ := os.UserHomeDir()
	binPath := filepath.Join(installDir, ".local", "share", "devai", "bin", "devai")
	ldflags := fmt.Sprintf("-X main.version=%s -X main.commit=source", tag)

	buildCmd := exec.Command("go", "build", "-ldflags", ldflags, "-o", binPath, "./cmd/devai")
	buildCmd.Dir = srcDir
	buildCmd.Stdout = os.Stdout
	buildCmd.Stderr = os.Stderr

	if err := buildCmd.Run(); err != nil {
		return fmt.Errorf("build failed: %w", err)
	}

	// Also update Python package
	fmt.Println("Updating ML package...")
	venvPip := filepath.Join(installDir, ".local", "share", "devai", "python", "venv", "bin", "pip")
	mlDir := filepath.Join(srcDir, "ml")
	pipCmd := exec.Command(venvPip, "install", "-e", mlDir, "-q")
	pipCmd.Stdout = os.Stdout
	pipCmd.Stderr = os.Stderr
	_ = pipCmd.Run() // non-fatal

	fmt.Printf("\nUpgraded to %s (built from source)\n", tag)
	return nil
}

// upgradeFromBinary downloads and installs a prebuilt binary, then reinstalls
// the matching devai_ml Python wheel from the same release so the Go CLI and the
// Python ML service never drift apart on an upgrade.
func upgradeFromBinary(url, tag string, release *releaseInfo) error {
	fmt.Printf("Downloading %s...\n", url)

	client := &http.Client{
		Timeout: 120 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 10 {
				return fmt.Errorf("too many redirects")
			}
			return nil
		},
	}

	resp, err := client.Get(url)
	if err != nil {
		return fmt.Errorf("downloading binary: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download failed (HTTP %d)", resp.StatusCode)
	}

	// Save to temp file
	tmpFile, err := os.CreateTemp("", "devai-upgrade-*.tar.gz")
	if err != nil {
		return fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()
	defer os.Remove(tmpPath)

	if _, err := io.Copy(tmpFile, resp.Body); err != nil {
		tmpFile.Close()
		return fmt.Errorf("saving download: %w", err)
	}

	fmt.Println("Extracting...")

	// Get install path
	homeDir, _ := os.UserHomeDir()
	binPath := filepath.Join(homeDir, ".local", "share", "devai", "bin", "devai")
	os.MkdirAll(filepath.Dir(binPath), 0o755)

	// Extract tar.gz (binary should be at top level)
	tmpFile.Close()

	// Extract only the devai binary from the archive
	extractCmd := exec.Command("tar", "xzf", tmpPath, "-C", filepath.Dir(binPath), "devai")
	if err := extractCmd.Run(); err != nil {
		return fmt.Errorf("extracting binary: %w", err)
	}
	os.Chmod(binPath, 0o755)

	// Keep the Python ML package in lockstep with the binary. Without this the
	// CLI updates but devai_ml stays on the old code, which silently drifts
	// (bug fixes / config in the ML service never land on `devai upgrade`).
	if err := reinstallMLWheel(release); err != nil {
		fmt.Printf("Warning: binary upgraded but the Python ML package was not updated: %v\n", err)
		fmt.Println("  Run the installer again to refresh it: curl -fsSL <install.sh> | bash")
	}

	fmt.Printf("Upgraded to %s\n", tag)
	return nil
}

// reinstallMLWheel downloads the devai_ml-*.whl asset from the release and
// force-reinstalls it into the devai venv. Deps (torch/onnx/...) are left as-is
// (--no-deps): they are pinned at install time and a code-only wheel bump does
// not need to re-resolve them. Non-fatal to the binary upgrade by design.
func reinstallMLWheel(release *releaseInfo) error {
	if release == nil {
		return fmt.Errorf("no release info")
	}

	var wheelURL string
	for _, asset := range release.Assets {
		if strings.HasPrefix(asset.Name, "devai_ml-") && strings.HasSuffix(asset.Name, ".whl") {
			wheelURL = asset.BrowserDownloadURL
			break
		}
	}
	if wheelURL == "" {
		return fmt.Errorf("no devai_ml wheel in release assets")
	}

	homeDir, _ := os.UserHomeDir()
	venvPip := filepath.Join(homeDir, ".local", "share", "devai", "python", "venv", "bin", "pip")
	if _, err := os.Stat(venvPip); err != nil {
		return fmt.Errorf("devai venv pip not found at %s (run the installer)", venvPip)
	}

	fmt.Println("Updating Python ML package...")
	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Get(wheelURL)
	if err != nil {
		return fmt.Errorf("downloading wheel: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("wheel download failed (HTTP %d)", resp.StatusCode)
	}

	tmpWheel, err := os.CreateTemp("", "devai_ml-*.whl")
	if err != nil {
		return fmt.Errorf("creating temp wheel: %w", err)
	}
	tmpWheelPath := tmpWheel.Name()
	defer os.Remove(tmpWheelPath)
	if _, err := io.Copy(tmpWheel, resp.Body); err != nil {
		tmpWheel.Close()
		return fmt.Errorf("saving wheel: %w", err)
	}
	tmpWheel.Close()

	pipCmd := exec.Command(venvPip, "install", "--force-reinstall", "--no-deps", tmpWheelPath)
	pipCmd.Stdout = os.Stdout
	pipCmd.Stderr = os.Stderr
	if err := pipCmd.Run(); err != nil {
		return fmt.Errorf("pip install failed: %w", err)
	}
	return nil
}
