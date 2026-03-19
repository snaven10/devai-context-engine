package branch

import (
	"fmt"
	"os/exec"
	"strings"
)

// Layer represents a search layer in the branch overlay hierarchy.
type Layer struct {
	Branch   string
	Priority int // lower = higher priority (0 = current, 1 = parent, etc.)
}

// Context manages branch-aware search resolution.
type Context struct {
	RepoPath     string
	ActiveBranch string
	Lineage      []Layer // ordered: current branch first, main last
}

// New creates a branch context for a repository.
func New(repoPath string) (*Context, error) {
	branch, err := currentBranch(repoPath)
	if err != nil {
		return nil, fmt.Errorf("detecting branch: %w", err)
	}

	ctx := &Context{
		RepoPath:     repoPath,
		ActiveBranch: branch,
	}

	ctx.Lineage = ctx.buildLineage(branch)
	return ctx, nil
}

// SwitchBranch changes the active branch context without git checkout.
func (c *Context) SwitchBranch(branch string) {
	c.ActiveBranch = branch
	c.Lineage = c.buildLineage(branch)
}

// BranchFilter returns the list of branches to search, ordered by priority.
// This is used as a metadata filter for vector store queries.
func (c *Context) BranchFilter() []string {
	branches := make([]string, len(c.Lineage))
	for i, l := range c.Lineage {
		branches[i] = l.Branch
	}
	return branches
}

// DeduplicateResults removes duplicate file entries, keeping the most specific branch version.
// Results must already be sorted by branch priority (current branch first).
func DeduplicateResults[T any](results []T, getBranch func(T) string, getFile func(T) string, branchPriority []string) []T {
	// Build priority map
	priority := make(map[string]int)
	for i, b := range branchPriority {
		priority[b] = i
	}

	// Track best result per file
	seen := make(map[string]int)    // file -> best priority
	bestIdx := make(map[string]int) // file -> index in results

	for i, r := range results {
		file := getFile(r)
		branch := getBranch(r)
		p, ok := priority[branch]
		if !ok {
			p = len(branchPriority) // lowest priority for unknown branches
		}

		if existingP, exists := seen[file]; !exists || p < existingP {
			seen[file] = p
			bestIdx[file] = i
		}
	}

	// Collect deduplicated results in original order
	keepSet := make(map[int]bool)
	for _, idx := range bestIdx {
		keepSet[idx] = true
	}

	deduped := make([]T, 0, len(keepSet))
	for i, r := range results {
		if keepSet[i] {
			deduped = append(deduped, r)
		}
	}
	return deduped
}

// IsTombstone checks if a vector point represents a deleted file.
func IsTombstone(metadata map[string]interface{}) bool {
	if isDel, ok := metadata["is_deletion"]; ok {
		if b, ok := isDel.(bool); ok {
			return b
		}
	}
	return false
}

// FilterTombstones removes results that represent deleted files in higher-priority branches.
func FilterTombstones[T any](results []T, getMetadata func(T) map[string]interface{}) []T {
	filtered := make([]T, 0, len(results))
	for _, r := range results {
		if !IsTombstone(getMetadata(r)) {
			filtered = append(filtered, r)
		}
	}
	return filtered
}

// buildLineage constructs the branch hierarchy.
// Order: current -> parent -> grandparent -> ... -> main
func (c *Context) buildLineage(branch string) []Layer {
	layers := []Layer{{Branch: branch, Priority: 0}}

	// Try to find parent branch via merge-base with main
	mainBranch := detectMainBranch(c.RepoPath)
	if mainBranch == "" {
		mainBranch = "main"
	}

	if branch == mainBranch {
		return layers
	}

	// Check for merge-base to detect parent
	mergeBase := getMergeBase(c.RepoPath, branch, mainBranch)
	if mergeBase != "" {
		// For now, simple two-level: feature branch -> main
		// TODO: detect intermediate branches via reflog
		layers = append(layers, Layer{Branch: mainBranch, Priority: 1})
	}

	return layers
}

// --- Git helpers ---

func currentBranch(repoPath string) (string, error) {
	out, err := gitCmd(repoPath, "rev-parse", "--abbrev-ref", "HEAD")
	if err != nil {
		return "", err
	}
	branch := strings.TrimSpace(out)
	if branch == "HEAD" {
		// Detached HEAD — use commit hash
		out, err = gitCmd(repoPath, "rev-parse", "--short", "HEAD")
		if err != nil {
			return "", err
		}
		return strings.TrimSpace(out), nil
	}
	return branch, nil
}

func detectMainBranch(repoPath string) string {
	// Check for 'main' first, then 'master'
	for _, candidate := range []string{"main", "master"} {
		if _, err := gitCmd(repoPath, "rev-parse", "--verify", candidate); err == nil {
			return candidate
		}
	}
	return ""
}

func getMergeBase(repoPath, branchA, branchB string) string {
	out, err := gitCmd(repoPath, "merge-base", branchA, branchB)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(out)
}

func gitCmd(repoPath string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", repoPath}, args...)...)
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return string(out), nil
}
