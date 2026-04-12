package tui

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

const devaiRepo = "snaven10/devai-context-engine"

type ghRelease struct {
	TagName string `json:"tag_name"`
}

// checkVersionAsync checks GitHub for a newer release in the background.
// Non-blocking: silently returns empty msg on any error.
func checkVersionAsync(currentVersion string) tea.Cmd {
	return func() tea.Msg {
		client := &http.Client{Timeout: 3 * time.Second}
		url := fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", devaiRepo)

		resp, err := client.Get(url)
		if err != nil {
			return versionCheckMsg{}
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			return versionCheckMsg{}
		}

		var release ghRelease
		if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
			return versionCheckMsg{}
		}

		return versionCheckMsg{
			LatestVersion: release.TagName,
			IsNewer:       isNewerVersion(currentVersion, release.TagName),
		}
	}
}

// isNewerVersion compares version strings (v0.1.2-alpha < v0.1.3-alpha).
func isNewerVersion(local, remote string) bool {
	local = strings.TrimPrefix(local, "v")
	remote = strings.TrimPrefix(remote, "v")
	if local == "dev" || local == "" {
		return true
	}
	return remote > local
}
