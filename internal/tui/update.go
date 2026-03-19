package tui

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

// ─── Update ──────────────────────────────────────────────────────────────────

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case tea.KeyMsg:
		// Global quit — always works
		if msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
		return m.handleKey(msg)

	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		return m, cmd

	// ─── Async data messages ────────────────────────────────────────────
	case dashboardLoadedMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.repos = msg.Repos
			m.errorMsg = ""
		}
		return m, nil

	case searchResultsMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.results = msg.Results
			m.resultIndex = 0
			m.screen = ScreenSearchResults
			m.errorMsg = ""
			m.statusMsg = ""
		}
		return m, nil

	case indexProgressMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.statusMsg = fmt.Sprintf(
				"Indexed %s: %d files, %d symbols, %d chunks (%.1fs)",
				filepath.Base(msg.RepoPath), msg.Files, msg.Symbols, msg.Chunks, msg.Duration,
			)
			m.errorMsg = ""
			// Reload dashboard
			return m, loadDashboard(m.client)
		}
		return m, nil

	case branchesLoadedMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.branches = msg.Branches
			m.branchIndex = 0
			m.screen = ScreenBranches
		}
		return m, nil

	case memoryResultsMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.memories = msg.Results
			m.memoryIndex = 0
		}
		return m, nil

	case historyLoadedMsg:
		m.loading = false
		if msg.Error != nil {
			m.errorMsg = msg.Error.Error()
		} else {
			m.events = msg.Events
			m.eventIndex = 0
		}
		return m, nil
	}

	return m, nil
}

// ─── Key Press Router ────────────────────────────────────────────────────────

func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.screen {
	case ScreenDashboard:
		return m.updateDashboard(msg)
	case ScreenRepos:
		return m.updateRepos(msg)
	case ScreenBranches:
		return m.updateBranches(msg)
	case ScreenSearch:
		return m.updateSearch(msg)
	case ScreenSearchResults:
		return m.updateSearchResults(msg)
	case ScreenHistory:
		return m.updateHistory(msg)
	case ScreenMemory:
		return m.updateMemory(msg)
	case ScreenDetail:
		return m.updateDetail(msg)
	case ScreenIndexRepo:
		return m.updateIndexRepo(msg)
	}
	return m, nil
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

func (m Model) updateDashboard(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "q":
		return m, tea.Quit
	case "j", "down":
		if m.menuIndex < len(m.menuItems)-1 {
			m.menuIndex++
		}
	case "k", "up":
		if m.menuIndex > 0 {
			m.menuIndex--
		}
	case "enter", " ":
		return m.selectMenuItem()
	case "s", "/":
		m.prevScreen = m.screen
		m.screen = ScreenSearch
		m.searchInput.Focus()
		return m, textinput.Blink
	case "r":
		// Refresh dashboard
		m.loading = true
		return m, loadDashboard(m.client)
	}
	return m, nil
}

func (m Model) selectMenuItem() (tea.Model, tea.Cmd) {
	switch m.menuIndex {
	case 0: // Search Code
		m.prevScreen = m.screen
		m.screen = ScreenSearch
		m.searchInput.Focus()
		return m, textinput.Blink
	case 1: // Repositories
		m.prevScreen = m.screen
		m.screen = ScreenRepos
		m.repoIndex = 0
		return m, nil
	case 2: // Branches
		m.prevScreen = m.screen
		m.screen = ScreenBranches
		// Build branch list from loaded repos
		m.branches = nil
		for _, r := range m.repos {
			m.branches = append(m.branches, BranchInfo{
				Name:       r.Branch,
				RepoName:   r.Name,
				RepoPath:   r.Path,
				IsActive:   true,
				IsIndexed:  r.IsIndexed,
				LastCommit: r.LastCommit,
				Files:      r.Files,
				Symbols:    r.Symbols,
			})
		}
		m.branchIndex = 0
		return m, nil
	case 3: // Memory
		m.prevScreen = m.screen
		m.screen = ScreenMemory
		m.memoryInput.Focus()
		m.loading = true
		// Load recent memories on enter
		return m, tea.Batch(textinput.Blink, loadRecentMemories(m.client))
	case 4: // Session History
		m.prevScreen = m.screen
		m.screen = ScreenHistory
		m.loading = true
		return m, loadHistory(m.client)
	case 5: // Index Repository
		m.prevScreen = m.screen
		m.screen = ScreenIndexRepo
		m.indexInput.Focus()
		m.indexInput.SetValue("")
		return m, textinput.Blink
	}
	return m, nil
}

// ─── Repositories ────────────────────────────────────────────────────────────

func (m Model) updateRepos(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "backspace":
		m.screen = ScreenDashboard
	case "q":
		return m, tea.Quit
	case "j", "down":
		if m.repoIndex < len(m.repos)-1 {
			m.repoIndex++
		}
	case "k", "up":
		if m.repoIndex > 0 {
			m.repoIndex--
		}
	case "enter":
		if m.repoIndex < len(m.repos) {
			// Show branches for selected repo
			r := m.repos[m.repoIndex]
			m.activeRepo = r.Path
			m.prevScreen = m.screen
			m.screen = ScreenBranches
			m.branches = []BranchInfo{{
				Name:       r.Branch,
				RepoName:   r.Name,
				RepoPath:   r.Path,
				IsActive:   true,
				IsIndexed:  r.IsIndexed,
				LastCommit: r.LastCommit,
				Files:      r.Files,
				Symbols:    r.Symbols,
			}}
			m.branchIndex = 0
		}
	case "i":
		// Index selected repo
		if m.repoIndex < len(m.repos) {
			m.loading = true
			m.statusMsg = "Indexing " + m.repos[m.repoIndex].Name + "..."
			return m, indexRepo(m.client, m.repos[m.repoIndex].Path)
		}
	case "/", "s":
		m.prevScreen = m.screen
		m.screen = ScreenSearch
		m.searchInput.Focus()
		return m, textinput.Blink
	}
	return m, nil
}

// ─── Branches ────────────────────────────────────────────────────────────────

func (m Model) updateBranches(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "backspace":
		m.screen = m.prevScreen
	case "q":
		return m, tea.Quit
	case "j", "down":
		if m.branchIndex < len(m.branches)-1 {
			m.branchIndex++
		}
	case "k", "up":
		if m.branchIndex > 0 {
			m.branchIndex--
		}
	}
	return m, nil
}

// ─── Search Input ────────────────────────────────────────────────────────────

func (m Model) updateSearch(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.screen = m.prevScreen
		m.searchInput.Blur()
	case "enter":
		query := m.searchInput.Value()
		if query != "" {
			m.searchQuery = query
			m.loading = true
			m.statusMsg = "Searching..."
			m.searchInput.Blur()
			return m, searchCode(m.client, query, 20)
		}
	default:
		var cmd tea.Cmd
		m.searchInput, cmd = m.searchInput.Update(msg)
		return m, cmd
	}
	return m, nil
}

// ─── Search Results ──────────────────────────────────────────────────────────

func (m Model) updateSearchResults(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "backspace":
		// Go back to search input so user can refine query
		m.screen = ScreenSearch
		m.searchInput.Focus()
		return m, textinput.Blink
	case "q":
		return m, tea.Quit
	case "j", "down":
		if m.resultIndex < len(m.results)-1 {
			m.resultIndex++
		}
	case "k", "up":
		if m.resultIndex > 0 {
			m.resultIndex--
		}
	case "enter":
		if m.resultIndex < len(m.results) {
			r := m.results[m.resultIndex]
			m.detailTitle = r.File + " — " + r.Symbol
			m.detailText = r.Text
			m.scrollOffset = 0
			m.prevScreen = m.screen
			m.screen = ScreenDetail
		}
	case "/", "s":
		m.screen = ScreenSearch
		m.searchInput.Focus()
		m.searchInput.SetValue("")
		return m, textinput.Blink
	}
	return m, nil
}

// ─── Session History ─────────────────────────────────────────────────────────

func (m Model) updateHistory(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "backspace":
		m.screen = ScreenDashboard
	case "q":
		return m, tea.Quit
	case "j", "down":
		if m.eventIndex < len(m.events)-1 {
			m.eventIndex++
		}
	case "k", "up":
		if m.eventIndex > 0 {
			m.eventIndex--
		}
	case "r":
		// Refresh history
		m.loading = true
		return m, loadHistory(m.client)
	case "enter":
		// Show event detail
		if m.eventIndex < len(m.events) {
			e := m.events[m.eventIndex]
			m.detailTitle = e.EventType + " — " + e.Tool
			m.detailText = fmt.Sprintf("Timestamp: %s\nEvent: %s\nTool: %s\nDuration: %dms\n\n%s",
				e.Timestamp, e.EventType, e.Tool, e.Duration, e.Summary)
			m.scrollOffset = 0
			m.prevScreen = m.screen
			m.screen = ScreenDetail
		}
	}
	return m, nil
}

// ─── Memory ──────────────────────────────────────────────────────────────────

func (m Model) updateMemory(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		if m.memoryInput.Focused() {
			m.memoryInput.Blur()
		} else {
			m.screen = ScreenDashboard
		}
	case "backspace":
		if m.memoryInput.Focused() {
			var cmd tea.Cmd
			m.memoryInput, cmd = m.memoryInput.Update(msg)
			return m, cmd
		}
		m.screen = ScreenDashboard
	case "q":
		if !m.memoryInput.Focused() {
			return m, tea.Quit
		}
		var cmd tea.Cmd
		m.memoryInput, cmd = m.memoryInput.Update(msg)
		return m, cmd
	case "enter":
		if m.memoryInput.Focused() {
			query := m.memoryInput.Value()
			if query != "" {
				m.loading = true
				m.memoryInput.Blur()
				return m, searchMemory(m.client, query)
			}
		} else if m.memoryIndex < len(m.memories) {
			mem := m.memories[m.memoryIndex]
			title := mem.Title
			if title == "" {
				title = truncate(mem.Content, 60)
			}
			m.detailTitle = fmt.Sprintf("[%s] %s", mem.Type, title)

			// Build rich detail text
			var detail strings.Builder
			if mem.Project != "" {
				detail.WriteString(fmt.Sprintf("Project: %s\n", mem.Project))
			}
			if mem.TopicKey != "" {
				detail.WriteString(fmt.Sprintf("Topic: %s\n", mem.TopicKey))
			}
			if mem.Tags != "" {
				detail.WriteString(fmt.Sprintf("Tags: %s\n", mem.Tags))
			}
			if mem.Files != "" {
				detail.WriteString(fmt.Sprintf("Files: %s\n", mem.Files))
			}
			if mem.RevisionCount > 1 {
				detail.WriteString(fmt.Sprintf("Revisions: %d\n", mem.RevisionCount))
			}
			if mem.CreatedAt != "" {
				detail.WriteString(fmt.Sprintf("Created: %s\n", mem.CreatedAt))
			}
			if mem.UpdatedAt != "" && mem.UpdatedAt != mem.CreatedAt {
				detail.WriteString(fmt.Sprintf("Updated: %s\n", mem.UpdatedAt))
			}
			detail.WriteString("\n" + mem.Content)
			m.detailText = detail.String()
			m.scrollOffset = 0
			m.prevScreen = m.screen
			m.screen = ScreenDetail
		}
	case "j", "down":
		if !m.memoryInput.Focused() && m.memoryIndex < len(m.memories)-1 {
			m.memoryIndex++
		}
	case "k", "up":
		if !m.memoryInput.Focused() && m.memoryIndex > 0 {
			m.memoryIndex--
		}
	case "/", "s":
		if !m.memoryInput.Focused() {
			m.memoryInput.Focus()
			return m, textinput.Blink
		}
		var cmd tea.Cmd
		m.memoryInput, cmd = m.memoryInput.Update(msg)
		return m, cmd
	default:
		if m.memoryInput.Focused() {
			var cmd tea.Cmd
			m.memoryInput, cmd = m.memoryInput.Update(msg)
			return m, cmd
		}
	}
	return m, nil
}

// ─── Index Repo ─────────────────────────────────────────────────────────────

func (m Model) updateIndexRepo(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.screen = ScreenDashboard
		m.indexInput.Blur()
		m.pathSuggestions = nil
		return m, nil
	case "enter":
		path := m.indexInput.Value()
		if path != "" {
			m.loading = true
			m.statusMsg = "Indexing " + filepath.Base(path) + "..."
			m.indexInput.Blur()
			m.screen = ScreenDashboard
			m.pathSuggestions = nil
			return m, indexRepo(m.client, path)
		}
	case "tab":
		// Tab completion: complete the current path
		current := m.indexInput.Value()
		if current == "" {
			current = "/"
		}
		suggestions := completePath(current)
		if len(suggestions) == 1 {
			// Single match — complete it
			m.indexInput.SetValue(suggestions[0])
			m.indexInput.CursorEnd()
			m.pathSuggestions = nil
		} else if len(suggestions) > 1 {
			// Multiple matches — show suggestions and complete common prefix
			prefix := commonPrefix(suggestions)
			if prefix != current {
				m.indexInput.SetValue(prefix)
				m.indexInput.CursorEnd()
			}
			m.pathSuggestions = suggestions
			m.pathSugIndex = 0
		}
		return m, nil
	case "down":
		// Cycle through suggestions
		if len(m.pathSuggestions) > 0 {
			m.pathSugIndex = (m.pathSugIndex + 1) % len(m.pathSuggestions)
			m.indexInput.SetValue(m.pathSuggestions[m.pathSugIndex])
			m.indexInput.CursorEnd()
			return m, nil
		}
	case "up":
		if len(m.pathSuggestions) > 0 {
			m.pathSugIndex--
			if m.pathSugIndex < 0 {
				m.pathSugIndex = len(m.pathSuggestions) - 1
			}
			m.indexInput.SetValue(m.pathSuggestions[m.pathSugIndex])
			m.indexInput.CursorEnd()
			return m, nil
		}
	default:
		var cmd tea.Cmd
		m.indexInput, cmd = m.indexInput.Update(msg)
		// Clear suggestions when typing
		m.pathSuggestions = nil
		return m, cmd
	}
	return m, nil
}

// completePath returns directory completions for the given partial path.
func completePath(partial string) []string {
	// Expand ~ to home dir
	if strings.HasPrefix(partial, "~") {
		home, _ := os.UserHomeDir()
		partial = home + partial[1:]
	}

	dir := filepath.Dir(partial)
	base := filepath.Base(partial)

	// If partial ends with /, list contents of that dir
	if strings.HasSuffix(partial, "/") {
		dir = partial
		base = ""
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}

	var matches []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		name := e.Name()
		if strings.HasPrefix(name, ".") && base == "" {
			continue // skip hidden dirs unless explicitly typing dot
		}
		if base == "" || strings.HasPrefix(strings.ToLower(name), strings.ToLower(base)) {
			full := filepath.Join(dir, name) + "/"
			matches = append(matches, full)
		}
	}
	sort.Strings(matches)
	return matches
}

// commonPrefix returns the longest common prefix of a list of strings.
func commonPrefix(strs []string) string {
	if len(strs) == 0 {
		return ""
	}
	prefix := strs[0]
	for _, s := range strs[1:] {
		for !strings.HasPrefix(s, prefix) {
			prefix = prefix[:len(prefix)-1]
			if prefix == "" {
				return ""
			}
		}
	}
	return prefix
}

// ─── Detail ──────────────────────────────────────────────────────────────────

func (m Model) updateDetail(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc", "backspace":
		m.screen = m.prevScreen
	case "q":
		return m, tea.Quit
	case "j", "down":
		m.scrollOffset++
	case "k", "up":
		if m.scrollOffset > 0 {
			m.scrollOffset--
		}
	}
	return m, nil
}
