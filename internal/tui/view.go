package tui

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ─── View (main router) ─────────────────────────────────────────────────────

func (m Model) View() string {
	var b strings.Builder

	// Header
	b.WriteString(styleHeader.Render("DevAI") + " ")
	b.WriteString(styleSubtitle.Render("Code Intelligence"))
	if m.version != "" && m.version != "dev" {
		b.WriteString(" " + styleResultLine.Render(m.version))
	}
	if m.latestVersion != "" {
		b.WriteString("  " + styleUpdateNotice.Render(
			fmt.Sprintf("Update available: %s (run: devai upgrade)", m.latestVersion),
		))
	}
	b.WriteString("\n")

	// Screen content
	switch m.screen {
	case ScreenDashboard:
		b.WriteString(m.viewDashboard())
	case ScreenRepos:
		b.WriteString(m.viewRepos())
	case ScreenBranches:
		b.WriteString(m.viewBranches())
	case ScreenSearch:
		b.WriteString(m.viewSearch())
	case ScreenSearchResults:
		b.WriteString(m.viewSearchResults())
	case ScreenHistory:
		b.WriteString(m.viewHistory())
	case ScreenMemory:
		b.WriteString(m.viewMemory())
	case ScreenDetail:
		b.WriteString(m.viewDetail())
	case ScreenIndexRepo:
		b.WriteString(m.viewIndexRepo())
	}

	// Status bar
	if m.errorMsg != "" {
		b.WriteString("\n" + styleError.Render("✗ " + m.errorMsg))
	} else if m.statusMsg != "" {
		b.WriteString("\n" + styleStatus.Render(m.statusMsg))
	}

	// Footer with key hints
	b.WriteString("\n" + m.viewKeyHelp())

	return styleApp.Render(b.String())
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

func (m Model) viewDashboard() string {
	var b strings.Builder

	// Stats cards
	if len(m.repos) > 0 {
		totalFiles := 0
		totalSymbols := 0
		totalChunks := 0
		for _, r := range m.repos {
			totalFiles += r.Files
			totalSymbols += r.Symbols
			totalChunks += r.Chunks
		}

		stats := fmt.Sprintf(
			"%s %d   %s %d   %s %d   %s %d",
			styleStatLabel.Render("Repos:"), len(m.repos),
			styleStatLabel.Render("Files:"), totalFiles,
			styleStatLabel.Render("Symbols:"), totalSymbols,
			styleStatLabel.Render("Chunks:"), totalChunks,
		)
		b.WriteString(styleCard.Render(stats) + "\n\n")
	} else if m.loading {
		b.WriteString(m.spinner.View() + " Loading...\n\n")
	} else {
		b.WriteString(styleCard.Render("No repositories indexed yet. Select 'Index Repository' to get started.") + "\n\n")
	}

	// Indexed repos summary
	if len(m.repos) > 0 {
		b.WriteString(styleTitle.Render("Indexed Repositories") + "\n")
		for _, r := range m.repos {
			name := r.Name
			if name == "" {
				name = r.Path
			}
			dot := statusDot(r.IsIndexed)
			b.WriteString(fmt.Sprintf("  %s%s  %s  %s\n",
				dot, name,
				styleResultLanguage.Render(r.Branch),
				styleResultLine.Render(fmt.Sprintf("%d files", r.Files)),
			))
		}
		b.WriteString("\n")
	}

	// Menu
	b.WriteString(styleTitle.Render("Actions") + "\n")
	for i, item := range m.menuItems {
		if i == m.menuIndex {
			b.WriteString(styleMenuItemSelected.Render(item) + "\n")
		} else {
			b.WriteString(styleMenuItemNormal.Render(item) + "\n")
		}
	}

	return b.String()
}

// ─── Repositories ────────────────────────────────────────────────────────────

func (m Model) viewRepos() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Repositories") + "\n\n")

	if len(m.repos) == 0 {
		b.WriteString(styleStatus.Render("No repositories indexed."))
		return b.String()
	}

	for i, r := range m.repos {
		cursor := "  "
		nameStyle := styleListItem
		if i == m.repoIndex {
			cursor = "▸ "
			nameStyle = styleListItemSelected
		}

		name := r.Name
		if name == "" {
			name = r.Path
		}

		b.WriteString(fmt.Sprintf("%s%s%s\n",
			cursor,
			statusDot(r.IsIndexed),
			nameStyle.Render(name),
		))
		b.WriteString(fmt.Sprintf("    %s  %s  %s  %s\n",
			styleResultLanguage.Render(r.Branch),
			styleResultLine.Render(fmt.Sprintf("%d files", r.Files)),
			styleResultLine.Render(fmt.Sprintf("%d symbols", r.Symbols)),
			styleResultLine.Render(r.IndexedAt),
		))
	}

	return b.String()
}

// ─── Branches ────────────────────────────────────────────────────────────────

func (m Model) viewBranches() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Branches") + "\n\n")

	if len(m.branches) == 0 {
		b.WriteString(styleStatus.Render("No branches indexed yet."))
		return b.String()
	}

	for i, br := range m.branches {
		cursor := "  "
		nameStyle := styleBranchNormal
		if i == m.branchIndex {
			cursor = "▸ "
			nameStyle = styleBranchActive
		}

		dot := statusDot(br.IsIndexed)

		repoLabel := br.RepoName
		if repoLabel == "" {
			repoLabel = br.RepoPath
		}
		b.WriteString(fmt.Sprintf("%s%s%s  %s\n",
			cursor, dot,
			nameStyle.Render(br.Name),
			styleResultLanguage.Render("["+repoLabel+"]"),
		))
		b.WriteString(fmt.Sprintf("    %s  %s  %s  %s\n",
			styleResultLine.Render(br.RepoPath),
			styleResultLine.Render(fmt.Sprintf("%d files", br.Files)),
			styleResultLine.Render(fmt.Sprintf("%d symbols", br.Symbols)),
			styleResultLine.Render(truncate(br.LastCommit, 8)),
		))
	}

	return b.String()
}

// ─── Search ──────────────────────────────────────────────────────────────────

func (m Model) viewSearch() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Semantic Search") + "\n\n")
	b.WriteString(styleSearchPrompt.Render("❯ ") + m.searchInput.View() + "\n")

	if m.loading {
		b.WriteString("\n" + m.spinner.View() + " Searching...")
	}

	return b.String()
}

// ─── Search Results ──────────────────────────────────────────────────────────

func (m Model) viewSearchResults() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render(fmt.Sprintf("Results for %q", m.searchQuery)))
	b.WriteString(styleSubtitle.Render(fmt.Sprintf(" — %d matches", len(m.results))) + "\n\n")

	if len(m.results) == 0 {
		b.WriteString(styleStatus.Render("No results found."))
		return b.String()
	}

	// Show results with scroll window
	visibleLines := m.height - 10
	if visibleLines < 5 {
		visibleLines = 20
	}

	start := 0
	if m.resultIndex > visibleLines/3 {
		start = m.resultIndex - visibleLines/3
	}

	for i := start; i < len(m.results) && i < start+visibleLines/3; i++ {
		r := m.results[i]
		cursor := "  "
		if i == m.resultIndex {
			cursor = "▸ "
		}

		// File:line — symbol (language) score
		line := fmt.Sprintf("%s%s %s",
			cursor,
			styleResultFile.Render(fmt.Sprintf("%s:%d-%d", r.File, r.StartLine, r.EndLine)),
			styleResultScore.Render(fmt.Sprintf("%.4f", r.Score)),
		)
		b.WriteString(line + "\n")

		if r.Symbol != "" {
			b.WriteString(fmt.Sprintf("    %s %s  %s\n",
				styleResultSymbol.Render(r.Symbol),
				styleResultLanguage.Render("("+r.Language+")"),
				styleResultLine.Render(r.SymbolType),
			))
		}

		// Code preview (first 4 lines for selected item)
		if i == m.resultIndex && r.Text != "" {
			lines := strings.Split(r.Text, "\n")
			preview := ""
			maxLines := 4
			for j, l := range lines {
				if j >= maxLines {
					break
				}
				if len(l) > 80 {
					l = l[:80] + "…"
				}
				preview += l + "\n"
			}
			b.WriteString(styleCodePreview.Render(strings.TrimRight(preview, "\n")) + "\n")
		}

		b.WriteString("\n")
	}

	return b.String()
}

// ─── Session History ─────────────────────────────────────────────────────────

func (m Model) viewHistory() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Session History") + "\n\n")

	if len(m.events) == 0 {
		b.WriteString(styleStatus.Render("No session events recorded yet."))
		return b.String()
	}

	for i, e := range m.events {
		cursor := "  "
		if i == m.eventIndex {
			cursor = "▸ "
		}
		b.WriteString(fmt.Sprintf("%s%s %s  %s  %s\n",
			cursor,
			styleResultLine.Render(e.Timestamp),
			styleResultSymbol.Render(e.Tool),
			styleResultLanguage.Render(e.EventType),
			truncate(e.Summary, 60),
		))
	}

	return b.String()
}

// ─── Memory ──────────────────────────────────────────────────────────────────

func (m Model) viewMemory() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Memory") + "\n\n")

	// Search input
	b.WriteString(styleSearchPrompt.Render("❯ ") + m.memoryInput.View() + "\n\n")

	if m.loading {
		b.WriteString(m.spinner.View() + " Searching memories...")
		return b.String()
	}

	if len(m.memories) == 0 {
		if m.loading {
			// Already showing spinner above
		} else if m.memoryInput.Value() != "" {
			b.WriteString(styleStatus.Render("No memories found."))
		} else {
			b.WriteString(styleStatus.Render("No memories stored yet. Use /s to search or type a query above."))
		}
		return b.String()
	}

	for i, mem := range m.memories {
		cursor := "  "
		if i == m.memoryIndex {
			cursor = "▸ "
		}
		// Show title (or truncated content if no title)
		displayText := mem.Title
		if displayText == "" {
			displayText = truncate(mem.Content, 50)
		}

		// Project badge
		project := ""
		if mem.Project != "" {
			project = styleSearchPrompt.Render("["+mem.Project+"]") + " "
		}

		// Revision indicator
		rev := ""
		if mem.RevisionCount > 1 {
			rev = fmt.Sprintf(" (rev %d)", mem.RevisionCount)
		}

		b.WriteString(fmt.Sprintf("%s%s %s%s%s\n",
			cursor,
			styleResultSymbol.Render(fmt.Sprintf("[%s]", mem.Type)),
			project,
			displayText,
			rev,
		))

		// Show topic_key on second line if selected
		if i == m.memoryIndex && mem.TopicKey != "" {
			b.WriteString(fmt.Sprintf("    %s  %s\n",
				styleResultLine.Render("topic: "+mem.TopicKey),
				styleResultLine.Render("tags: "+mem.Tags),
			))
		}
	}

	return b.String()
}

// ─── Detail ──────────────────────────────────────────────────────────────────

func (m Model) viewDetail() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render(m.detailTitle) + "\n\n")

	lines := strings.Split(m.detailText, "\n")
	visibleHeight := m.height - 8
	if visibleHeight < 10 {
		visibleHeight = 30
	}

	start := m.scrollOffset
	if start > len(lines) {
		start = len(lines) - 1
	}
	if start < 0 {
		start = 0
	}

	end := start + visibleHeight
	if end > len(lines) {
		end = len(lines)
	}

	for i := start; i < end; i++ {
		line := lines[i]
		if len(line) > m.width-6 && m.width > 6 {
			line = line[:m.width-6]
		}
		b.WriteString(styleCodePreview.Render(line) + "\n")
	}

	if end < len(lines) {
		b.WriteString(styleStatus.Render(fmt.Sprintf("\n  ↓ %d more lines", len(lines)-end)))
	}

	return b.String()
}

// ─── Index Repo ─────────────────────────────────────────────────────────────

func (m Model) viewIndexRepo() string {
	var b strings.Builder
	b.WriteString(styleTitle.Render("Index New Repository") + "\n\n")
	b.WriteString("Enter the full path to a git repository:\n\n")
	b.WriteString(styleSearchPrompt.Render("❯ ") + m.indexInput.View() + "\n")

	// Show path suggestions
	if len(m.pathSuggestions) > 0 {
		b.WriteString("\n" + styleSubtitle.Render("Suggestions (tab/↑↓ to cycle):") + "\n")
		maxShow := 8
		if len(m.pathSuggestions) < maxShow {
			maxShow = len(m.pathSuggestions)
		}
		for i := 0; i < maxShow; i++ {
			cursor := "  "
			nameStyle := styleListItem
			if i == m.pathSugIndex {
				cursor = "▸ "
				nameStyle = styleListItemSelected
			}
			// Check if it's a git repo
			gitMarker := ""
			if isGitRepo(m.pathSuggestions[i]) {
				gitMarker = " " + styleAccent.Render("[git]")
			}
			b.WriteString(cursor + nameStyle.Render(m.pathSuggestions[i]) + gitMarker + "\n")
		}
		if len(m.pathSuggestions) > maxShow {
			b.WriteString(styleStatus.Render(fmt.Sprintf("  ... and %d more", len(m.pathSuggestions)-maxShow)) + "\n")
		}
	}

	if m.loading {
		b.WriteString("\n" + m.spinner.View() + " Indexing...")
	}

	if len(m.repos) > 0 {
		b.WriteString("\n" + styleSubtitle.Render("Already indexed:") + "\n")
		for _, r := range m.repos {
			b.WriteString(fmt.Sprintf("  %s%s  %s\n",
				statusDot(r.IsIndexed),
				r.Name,
				styleResultLine.Render(r.Path),
			))
		}
	}

	return b.String()
}

// ─── Helpers ────────────────────────────────────────────────────────────────

func isGitRepo(path string) bool {
	p := strings.TrimSuffix(path, "/")
	_, err := os.Stat(filepath.Join(p, ".git"))
	return err == nil
}

// ─── Key Help ────────────────────────────────────────────────────────────────

func (m Model) viewKeyHelp() string {
	switch m.screen {
	case ScreenDashboard:
		return styleFooter.Render(
			keyHelp("j/k", "navigate") + "  " +
				keyHelp("enter", "select") + "  " +
				keyHelp("s", "search") + "  " +
				keyHelp("r", "refresh") + "  " +
				keyHelp("q", "quit"),
		)
	case ScreenSearch:
		return styleFooter.Render(
			keyHelp("enter", "search") + "  " +
				keyHelp("esc", "back"),
		)
	case ScreenSearchResults:
		return styleFooter.Render(
			keyHelp("j/k", "navigate") + "  " +
				keyHelp("enter", "detail") + "  " +
				keyHelp("/", "new search") + "  " +
				keyHelp("esc", "back"),
		)
	case ScreenRepos:
		return styleFooter.Render(
			keyHelp("j/k", "navigate") + "  " +
				keyHelp("enter", "branches") + "  " +
				keyHelp("i", "index") + "  " +
				keyHelp("esc", "back"),
		)
	case ScreenDetail:
		return styleFooter.Render(
			keyHelp("j/k", "scroll") + "  " +
				keyHelp("esc", "back"),
		)
	case ScreenIndexRepo:
		return styleFooter.Render(
			keyHelp("enter", "index") + "  " +
				keyHelp("esc", "back"),
		)
	default:
		return styleFooter.Render(
			keyHelp("j/k", "navigate") + "  " +
				keyHelp("esc", "back") + "  " +
				keyHelp("q", "quit"),
		)
	}
}
