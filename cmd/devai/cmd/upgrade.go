package cmd

import (
	"encoding/json"
	"fmt"
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

	return upgradeFromBinary(downloadURL, release.TagName)
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

// upgradeFromBinary downloads and installs a prebuilt binary.
func upgradeFromBinary(url, tag string) error {
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

	fmt.Println("Extracting...")

	// Get install path
	homeDir, _ := os.UserHomeDir()
	binPath := filepath.Join(homeDir, ".local", "share", "devai", "bin", "devai")
	os.MkdirAll(filepath.Dir(binPath), 0o755)

	// Extract tar.gz (binary should be at top level)
	tmpFile.Close()

	// Use tar command for simplicity
	extractCmd := exec.Command("tar", "xzf", tmpPath, "-C", filepath.Dir(binPath))
	if err := extractCmd.Run(); err != nil {
		return fmt.Errorf("extracting binary: %w", err)
	}

	fmt.Printf("Upgraded to %s\n", tag)
	return nil
}
