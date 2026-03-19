package tui

import "github.com/charmbracelet/lipgloss"

// ─── Colors (DevAI dark theme — cyan/teal accents) ──────────────────────────

var (
	colorBase    = lipgloss.Color("#1a1b26") // deep navy
	colorSurface = lipgloss.Color("#24283b") // card background
	colorOverlay = lipgloss.Color("#414868") // borders, muted
	colorMuted   = lipgloss.Color("#565f89") // secondary text
	colorText    = lipgloss.Color("#c0caf5") // primary text
	colorPrimary = lipgloss.Color("#7dcfff") // cyan — primary accent
	colorAccent  = lipgloss.Color("#9ece6a") // green — success/indexed
	colorWarm    = lipgloss.Color("#e0af68") // yellow — warnings
	colorRed     = lipgloss.Color("#f7768e") // red — errors
	colorPurple  = lipgloss.Color("#bb9af7") // purple — symbols
)

// ─── App Layout ─────────────────────────────────────────────────────────────

var (
	styleApp = lipgloss.NewStyle().
		Padding(1, 2)

	styleHeader = lipgloss.NewStyle().
			Foreground(colorPrimary).
			Bold(true).
			Padding(0, 0, 1, 0)

	styleFooter = lipgloss.NewStyle().
			Foreground(colorMuted).
			Padding(1, 0, 0, 0)

	styleTitle = lipgloss.NewStyle().
			Foreground(colorPrimary).
			Bold(true)

	styleSubtitle = lipgloss.NewStyle().
			Foreground(colorMuted).
			Italic(true)
)

// ─── Menu Items ─────────────────────────────────────────────────────────────

var (
	styleMenuItem = lipgloss.NewStyle().
			Foreground(colorText).
			Padding(0, 2)

	styleMenuItemSelected = lipgloss.NewStyle().
				Foreground(colorPrimary).
				Bold(true).
				Padding(0, 2).
				SetString("▸ ")

	styleMenuItemNormal = lipgloss.NewStyle().
				Foreground(colorText).
				Padding(0, 2).
				SetString("  ")
)

// ─── List Items ─────────────────────────────────────────────────────────────

var (
	styleListItem = lipgloss.NewStyle().
			Foreground(colorText)

	styleListItemSelected = lipgloss.NewStyle().
				Foreground(colorPrimary).
				Bold(true)

	styleListIndex = lipgloss.NewStyle().
			Foreground(colorMuted).
			Width(4)
)

// ─── Cards / Panels ─────────────────────────────────────────────────────────

var (
	styleCard = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(colorOverlay).
			Padding(1, 2)

	styleCardTitle = lipgloss.NewStyle().
			Foreground(colorPrimary).
			Bold(true)

	styleStatLabel = lipgloss.NewStyle().
			Foreground(colorMuted)

	styleStatValue = lipgloss.NewStyle().
			Foreground(colorText).
			Bold(true)
)

// ─── Accent ─────────────────────────────────────────────────────────────────

var styleAccent = lipgloss.NewStyle().Foreground(colorAccent)

// ─── Search ─────────────────────────────────────────────────────────────────

var (
	styleSearchPrompt = lipgloss.NewStyle().
				Foreground(colorPrimary).
				Bold(true)

	styleSearchInput = lipgloss.NewStyle().
				Foreground(colorText)
)

// ─── Results ────────────────────────────────────────────────────────────────

var (
	styleResultFile = lipgloss.NewStyle().
			Foreground(colorPrimary)

	styleResultSymbol = lipgloss.NewStyle().
				Foreground(colorPurple).
				Bold(true)

	styleResultLanguage = lipgloss.NewStyle().
				Foreground(colorAccent)

	styleResultScore = lipgloss.NewStyle().
				Foreground(colorWarm)

	styleResultLine = lipgloss.NewStyle().
			Foreground(colorMuted)

	styleCodePreview = lipgloss.NewStyle().
				Foreground(colorText).
				Border(lipgloss.NormalBorder(), false, false, false, true).
				BorderForeground(colorOverlay).
				PaddingLeft(1).
				MarginLeft(2)
)

// ─── Status Indicators ──────────────────────────────────────────────────────

var (
	styleIndexed = lipgloss.NewStyle().
			Foreground(colorAccent).
			SetString("● ")

	styleNotIndexed = lipgloss.NewStyle().
			Foreground(colorRed).
			SetString("○ ")

	styleActive = lipgloss.NewStyle().
			Foreground(colorPrimary).
			SetString("◆ ")

	styleLoading = lipgloss.NewStyle().
			Foreground(colorWarm)

	styleError = lipgloss.NewStyle().
			Foreground(colorRed).
			Bold(true)

	styleSuccess = lipgloss.NewStyle().
			Foreground(colorAccent).
			Bold(true)

	styleStatus = lipgloss.NewStyle().
			Foreground(colorMuted).
			Italic(true)
)

// ─── Branch View ────────────────────────────────────────────────────────────

var (
	styleBranchActive = lipgloss.NewStyle().
				Foreground(colorPrimary).
				Bold(true)

	styleBranchIndexed = lipgloss.NewStyle().
				Foreground(colorAccent)

	styleBranchNormal = lipgloss.NewStyle().
				Foreground(colorText)
)

// ─── Key Help ───────────────────────────────────────────────────────────────

var (
	styleKeyHelp = lipgloss.NewStyle().
			Foreground(colorMuted)

	styleKeyBind = lipgloss.NewStyle().
			Foreground(colorPrimary).
			Bold(true)
)

// ─── Helpers ────────────────────────────────────────────────────────────────

func keyHelp(key, desc string) string {
	return styleKeyBind.Render(key) + " " + styleKeyHelp.Render(desc)
}

func statusDot(indexed bool) string {
	if indexed {
		return styleIndexed.String()
	}
	return styleNotIndexed.String()
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	if max <= 3 {
		return s[:max]
	}
	return s[:max-3] + "..."
}
