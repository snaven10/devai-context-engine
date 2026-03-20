package cmd

import (
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/snaven10/devai/internal/mlclient"
	"github.com/spf13/cobra"
)

var watchCmd = &cobra.Command{
	Use:   "watch [repo-path]",
	Short: "Watch repository for changes and auto-index",
	Long: `Watch the repository for file changes and trigger incremental
indexing automatically. Uses fsnotify for efficient file system monitoring.
Debounces changes to avoid excessive re-indexing.`,
	Args: cobra.MaximumNArgs(1),
	RunE: runWatch,
}

func init() {
	watchCmd.Flags().Duration("debounce", 5*time.Second, "Debounce interval before re-indexing")
	watchCmd.Flags().StringSlice("exclude", []string{
		"node_modules", "vendor", ".git", "__pycache__", "dist", "build",
		".devai", ".next", ".nuxt", "target", ".gradle",
	}, "Directories to exclude from watching")
	rootCmd.AddCommand(watchCmd)
}

func runWatch(cmd *cobra.Command, args []string) error {
	repoPath := "."
	if len(args) > 0 {
		repoPath = args[0]
	}

	absPath, err := filepath.Abs(repoPath)
	if err != nil {
		return fmt.Errorf("resolving path: %w", err)
	}

	// Verify git repo
	if _, err := os.Stat(filepath.Join(absPath, ".git")); os.IsNotExist(err) {
		return fmt.Errorf("%s is not a git repository", absPath)
	}

	debounce, _ := cmd.Flags().GetDuration("debounce")
	excludeDirs, _ := cmd.Flags().GetStringSlice("exclude")
	excludeSet := make(map[string]bool)
	for _, d := range excludeDirs {
		excludeSet[d] = true
	}

	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return fmt.Errorf("creating watcher: %w", err)
	}
	defer watcher.Close()

	// Walk directories and add to watcher (skip excluded)
	dirCount := 0
	err = filepath.Walk(absPath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil // skip errors
		}
		if info.IsDir() {
			name := info.Name()
			if excludeSet[name] || strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			watcher.Add(path)
			dirCount++
		}
		return nil
	})
	if err != nil {
		return fmt.Errorf("walking directory: %w", err)
	}

	fmt.Printf("Watching %s (%d directories)\n", absPath, dirCount)
	fmt.Printf("Debounce: %s\n", debounce)
	fmt.Println("Press Ctrl+C to stop")

	// Debounce timer
	var timer *time.Timer
	pendingChanges := make(map[string]bool)

	// Signal handling
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	triggerIndex := func() {
		if len(pendingChanges) == 0 {
			return
		}

		fileCount := len(pendingChanges)
		fmt.Printf("\n[%s] %d file(s) changed, triggering index...\n",
			time.Now().Format("15:04:05"), fileCount)

		// Clear pending
		pendingChanges = make(map[string]bool)

		// Call ML service
		client, err := mlclient.NewStdioClient()
		if err != nil {
			fmt.Printf("  Error connecting to ML service: %v\n", err)
			return
		}

		result, err := client.Call("index_repo", map[string]interface{}{
			"repo_path":   absPath,
			"incremental": true,
		})
		client.Close()

		if err != nil {
			fmt.Printf("  Index error: %v\n", err)
			return
		}

		if m, ok := result.(map[string]interface{}); ok {
			fmt.Printf("  Indexed: %v files, %v chunks, %v symbols (%.1fs)\n",
				m["files_processed"], m["chunks_created"], m["symbols_found"],
				m["duration_seconds"])
		}
	}

	for {
		select {
		case event, ok := <-watcher.Events:
			if !ok {
				return nil
			}

			// Only care about writes, creates, removes
			if event.Op&(fsnotify.Write|fsnotify.Create|fsnotify.Remove|fsnotify.Rename) == 0 {
				continue
			}

			// Skip hidden files, lock files, etc
			base := filepath.Base(event.Name)
			if strings.HasPrefix(base, ".") || strings.HasSuffix(base, "~") ||
				strings.HasSuffix(base, ".swp") || strings.HasSuffix(base, ".tmp") {
				continue
			}

			pendingChanges[event.Name] = true

			// Reset debounce timer
			if timer != nil {
				timer.Stop()
			}
			timer = time.AfterFunc(debounce, triggerIndex)

		case err, ok := <-watcher.Errors:
			if !ok {
				return nil
			}
			fmt.Fprintf(os.Stderr, "Watcher error: %v\n", err)

		case <-sigCh:
			fmt.Println("\nStopping watcher...")
			if timer != nil {
				timer.Stop()
			}
			return nil
		}
	}
}
