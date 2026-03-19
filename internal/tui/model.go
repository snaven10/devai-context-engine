// Package tui implements the Bubbletea terminal UI for DevAI.
//
// Following the Gentleman Bubbletea patterns:
// - Screen constants as iota
// - Single Model struct holds ALL state
// - Update() with type switch
// - Per-screen key handlers returning (tea.Model, tea.Cmd)
// - Vim keys (j/k) for navigation
// - PrevScreen for back navigation
package tui

import (
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/snaven10/devai/internal/mlclient"
)

// ─── Screens ─────────────────────────────────────────────────────────────────

type Screen int

const (
	ScreenDashboard Screen = iota
	ScreenRepos
	ScreenBranches
	ScreenSearch
	ScreenSearchResults
	ScreenHistory
	ScreenMemory
	ScreenDetail
	ScreenIndexRepo
)

// ─── Custom Messages ─────────────────────────────────────────────────────────

type (
	dashboardLoadedMsg struct {
		Repos []RepoInfo
		Error error
	}
	searchResultsMsg struct {
		Query   string
		Results []SearchResult
		Error   error
	}
	indexStartedMsg struct {
		RepoPath string
	}
	indexProgressMsg struct {
		RepoPath string
		Status   string
		Files    int
		Chunks   int
		Symbols  int
		Duration float64
		Error    error
	}
	branchesLoadedMsg struct {
		RepoPath string
		Branches []BranchInfo
		Error    error
	}
	memoryResultsMsg struct {
		Results []MemoryEntry
		Error   error
	}
	historyLoadedMsg struct {
		Events []SessionEvent
		Error  error
	}
)

// ─── Data Types ──────────────────────────────────────────────────────────────

type RepoInfo struct {
	Path       string
	Name       string
	Branch     string
	LastCommit string
	Files      int
	Symbols    int
	Chunks     int
	IndexedAt  string
	IsIndexed  bool
}

type SearchResult struct {
	File       string
	Symbol     string
	SymbolType string
	Language   string
	StartLine  int
	EndLine    int
	Score      float64
	Text       string
	Branch     string
}

type BranchInfo struct {
	Name       string
	RepoName   string
	RepoPath   string
	IsActive   bool
	IsIndexed  bool
	LastCommit string
	Files      int
	Symbols    int
}

type MemoryEntry struct {
	ID            int
	Title         string
	Content       string
	Type          string
	Scope         string
	Project       string
	TopicKey      string
	Tags          string
	Files         string
	RevisionCount int
	Score         float64
	CreatedAt     string
	UpdatedAt     string
}

type SessionEvent struct {
	Timestamp string
	EventType string
	Tool      string
	Summary   string
	Duration  int
}

// ─── Model ───────────────────────────────────────────────────────────────────

type Model struct {
	client  *mlclient.StdioClient
	version string
	width   int
	height  int

	// Navigation
	screen     Screen
	prevScreen Screen

	// Dashboard
	repos     []RepoInfo
	menuIndex int
	menuItems []string

	// Repos view
	repoIndex int

	// Branches
	branches    []BranchInfo
	branchIndex int
	activeRepo  string

	// Search
	searchInput textinput.Model
	searchQuery string
	results     []SearchResult
	resultIndex int

	// Memory
	memoryInput textinput.Model
	memories    []MemoryEntry
	memoryIndex int

	// History
	events     []SessionEvent
	eventIndex int

	// Detail
	detailText   string
	detailTitle  string
	scrollOffset int

	// Index repo input
	indexInput      textinput.Model
	pathSuggestions []string
	pathSugIndex    int

	// State
	loading   bool
	spinner   spinner.Model
	statusMsg string
	errorMsg  string
}

// New creates a new TUI model connected to the given MCP client.
func New(client *mlclient.StdioClient, version string) Model {
	si := textinput.New()
	si.Placeholder = "Search code..."
	si.CharLimit = 256
	si.Width = 60

	mi := textinput.New()
	mi.Placeholder = "Search memories..."
	mi.CharLimit = 256
	mi.Width = 60

	ii := textinput.New()
	ii.Placeholder = "/path/to/repository"
	ii.CharLimit = 512
	ii.Width = 70

	sp := spinner.New()
	sp.Spinner = spinner.Dot

	return Model{
		client:    client,
		version:   version,
		screen:    ScreenDashboard,
		menuIndex: 0,
		menuItems: []string{
			"Search Code",
			"Repositories",
			"Branches",
			"Memory",
			"Session History",
			"Index Repository",
		},
		searchInput: si,
		memoryInput: mi,
		indexInput:  ii,
		spinner:     sp,
	}
}

// Init loads initial data (dashboard repos + spinner).
func (m Model) Init() tea.Cmd {
	return tea.Batch(
		m.spinner.Tick,
		loadDashboard(m.client),
	)
}

// ─── Commands (async data loading) ──────────────────────────────────────────

func loadDashboard(client *mlclient.StdioClient) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("index_status", map[string]interface{}{})
		if err != nil {
			return dashboardLoadedMsg{Error: err}
		}

		// Parse the result into RepoInfo list
		repos := parseRepoInfoFromResult(result)
		return dashboardLoadedMsg{Repos: repos}
	}
}

func searchCode(client *mlclient.StdioClient, query string, limit int) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("search", map[string]interface{}{
			"query": query,
			"limit": limit,
		})
		if err != nil {
			return searchResultsMsg{Query: query, Error: err}
		}

		results := parseSearchResults(result)
		return searchResultsMsg{Query: query, Results: results}
	}
}

func loadHistory(client *mlclient.StdioClient) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("get_session_history", map[string]interface{}{
			"limit": 50,
		})
		if err != nil {
			return historyLoadedMsg{Error: err}
		}
		events := parseSessionEvents(result)
		return historyLoadedMsg{Events: events}
	}
}

func searchMemory(client *mlclient.StdioClient, query string) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("recall", map[string]interface{}{
			"query": query,
			"limit": 20,
		})
		if err != nil {
			return memoryResultsMsg{Error: err}
		}
		memories := parseMemoryEntries(result)
		return memoryResultsMsg{Results: memories}
	}
}

func loadRecentMemories(client *mlclient.StdioClient) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("memory_context", map[string]interface{}{
			"limit": 20,
		})
		if err != nil {
			return memoryResultsMsg{Error: err}
		}
		memories := parseMemoryEntries(result)
		return memoryResultsMsg{Results: memories}
	}
}

func indexRepo(client *mlclient.StdioClient, path string) tea.Cmd {
	return func() tea.Msg {
		result, err := client.Call("index_repo", map[string]interface{}{
			"repo_path":   path,
			"incremental": true,
		})
		if err != nil {
			return indexProgressMsg{RepoPath: path, Error: err}
		}

		// Parse result
		m, ok := result.(map[string]interface{})
		if !ok {
			return indexProgressMsg{RepoPath: path, Status: "completed"}
		}
		files, _ := m["files_processed"].(float64)
		chunks, _ := m["chunks_created"].(float64)
		symbols, _ := m["symbols_found"].(float64)
		duration, _ := m["duration_seconds"].(float64)

		return indexProgressMsg{
			RepoPath: path,
			Status:   "completed",
			Files:    int(files),
			Chunks:   int(chunks),
			Symbols:  int(symbols),
			Duration: duration,
		}
	}
}

// ─── Parse Helpers ──────────────────────────────────────────────────────────

func parseRepoInfoFromResult(result interface{}) []RepoInfo {
	var repos []RepoInfo

	m, ok := result.(map[string]interface{})
	if !ok {
		return repos
	}

	// Python index_status returns {"count": N, "repos": [...]}
	items, ok := m["repos"].([]interface{})
	if !ok {
		return repos
	}

	for _, item := range items {
		r, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		repos = append(repos, RepoInfo{
			Path:      getString(r, "repo"),
			Name:      getString(r, "name"),
			Branch:    getString(r, "branch"),
			LastCommit: getString(r, "last_commit"),
			Files:     getInt(r, "files"),
			Symbols:   getInt(r, "symbols"),
			Chunks:    getInt(r, "chunks"),
			IndexedAt: getString(r, "indexed_at"),
			IsIndexed: getString(r, "status") == "indexed",
		})
	}

	return repos
}

func parseSearchResults(result interface{}) []SearchResult {
	var results []SearchResult

	m, ok := result.(map[string]interface{})
	if !ok {
		return results
	}

	// The result is the JSON text from MCP
	items, ok := m["results"].([]interface{})
	if !ok {
		return results
	}

	for _, item := range items {
		r, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		results = append(results, SearchResult{
			File:       getString(r, "file"),
			Symbol:     getString(r, "symbol"),
			SymbolType: getString(r, "symbol_type"),
			Language:   getString(r, "language"),
			StartLine:  getInt(r, "start_line"),
			EndLine:    getInt(r, "end_line"),
			Score:      getFloat(r, "score"),
			Text:       getString(r, "text"),
			Branch:     getString(r, "branch"),
		})
	}
	return results
}

func parseSessionEvents(result interface{}) []SessionEvent {
	var events []SessionEvent

	m, ok := result.(map[string]interface{})
	if !ok {
		return events
	}

	items, ok := m["events"].([]interface{})
	if !ok {
		return events
	}

	for _, item := range items {
		e, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		events = append(events, SessionEvent{
			Timestamp: getString(e, "timestamp"),
			EventType: getString(e, "event_type"),
			Tool:      getString(e, "tool"),
			Summary:   getString(e, "summary"),
		})
	}
	return events
}

func parseMemoryEntries(result interface{}) []MemoryEntry {
	var entries []MemoryEntry

	m, ok := result.(map[string]interface{})
	if !ok {
		return entries
	}

	items, ok := m["memories"].([]interface{})
	if !ok {
		return entries
	}

	for _, item := range items {
		e, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		// Content field: new API uses "content", old uses "text"
		content := getString(e, "content")
		if content == "" {
			content = getString(e, "text")
		}
		entries = append(entries, MemoryEntry{
			ID:            getInt(e, "id"),
			Title:         getString(e, "title"),
			Content:       content,
			Type:          getString(e, "type"),
			Scope:         getString(e, "scope"),
			Project:       getString(e, "project"),
			TopicKey:      getString(e, "topic_key"),
			Tags:          getString(e, "tags"),
			Files:         getString(e, "files"),
			RevisionCount: getInt(e, "revision_count"),
			Score:         getFloat(e, "score"),
			CreatedAt:     getString(e, "created_at"),
			UpdatedAt:     getString(e, "updated_at"),
		})
	}
	return entries
}

func getInt(m map[string]interface{}, key string) int {
	v, ok := m[key]
	if !ok {
		return 0
	}
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	}
	return 0
}

func getFloat(m map[string]interface{}, key string) float64 {
	v, ok := m[key]
	if !ok {
		return 0
	}
	f, ok := v.(float64)
	if !ok {
		return 0
	}
	return f
}

func getString(m map[string]interface{}, key string) string {
	v, ok := m[key]
	if !ok {
		return ""
	}
	s, ok := v.(string)
	if !ok {
		return ""
	}
	return s
}

