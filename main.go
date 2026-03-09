package main

import (
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"xc/vfs"

	"github.com/gdamore/tcell"
)

// --- Styles ---

var (
	styleDef    = tcell.StyleDefault.Background(tcell.ColorNavy).Foreground(tcell.ColorWhite)
	styleDir    = styleDef.Bold(true)
	styleCursor = tcell.StyleDefault.Background(tcell.ColorTeal).Foreground(tcell.ColorBlack)
	styleTagged = tcell.StyleDefault.Background(tcell.ColorNavy).Foreground(tcell.ColorYellow)
	styleBorder = styleDef
	styleStatus = tcell.StyleDefault.Background(tcell.ColorTeal).Foreground(tcell.ColorBlack)
)

// --- Panel ---

// vfsEntry stores state for returning from a nested VFS.
type vfsEntry struct {
	fs        vfs.VFS
	path      string
	cursor    int
	offset    int
	entryPath string // display path of the entity we entered (e.g., "/home/user/archive.tar")
}

// Panel represents one file list panel.
type Panel struct {
	path     string
	files    []vfs.File
	cursor   int
	offset   int
	fs       vfs.VFS
	stack    []vfsEntry // VFS context stack for nested filesystems
	probes   []vfs.VFS  // available VFS implementations to probe
	tagged   map[string]bool
	dirSizes map[string]int64 // calculated recursive directory sizes
	onError  func(string)
	onExec   func(string) // run a command from the panel's working directory
}

func newPanel(path string, fs vfs.VFS, probes []vfs.VFS, onError func(string), onExec func(string)) *Panel {
	p := &Panel{path: path, fs: fs, probes: probes, onError: onError, onExec: onExec}
	p.loadDir()
	return p
}

func (p *Panel) reportErr(err error) {
	if err != nil {
		slog.Error("panel error", "path", p.path, "err", err)
		if p.onError != nil {
			p.onError(err.Error())
		}
	}
}

func (p *Panel) loadDir() {
	p.tagged = nil
	p.dirSizes = nil
	files, err := p.fs.ReadDir(p.path)
	if err != nil {
		p.reportErr(err)
		p.files = nil
		return
	}

	// Prepend ".." entry unless at local FS root.
	showDotDot := true
	if _, ok := p.fs.(*vfs.LocalFS); ok && filepath.Dir(p.path) == p.path {
		showDotDot = false
	}
	if showDotDot {
		dotdot := vfs.NewFile("..", 0, vfs.TypeDir, time.Time{})
		p.files = append([]vfs.File{dotdot}, files...)
	} else {
		p.files = files
	}
}

func (p *Panel) enter() {
	if p.cursor >= len(p.files) {
		return
	}
	f := p.files[p.cursor]

	if f.Name() == ".." {
		p.goUp()
		return
	}

	if f.IsDir() {
		p.path = filepath.Join(p.path, f.Name())
		p.cursor = 0
		p.offset = 0
		p.loadDir()
		return
	}

	// Symlink pointing to a directory — enter the target.
	if f.IsSymlink() {
		if dp := p.diskPath(f.Name()); dp != "" {
			if resolved, err := filepath.EvalSymlinks(dp); err == nil {
				if info, err := os.Stat(resolved); err == nil && info.IsDir() {
					p.path = resolved
					p.cursor = 0
					p.offset = 0
					p.loadDir()
					return
				}
			}
		}
	}

	// Run executable files.
	if f.IsExecutable() && p.onExec != nil {
		if dp := p.diskPath(f.Name()); dp != "" {
			p.onExec(shellQuote(dp))
			return
		}
	}

	// Try to enter the file as a virtual filesystem.
	fullPath := p.diskPath(f.Name())
	if fullPath == "" {
		return
	}
	header := readHeader(fullPath, 32)
	for _, probe := range p.probes {
		if !probe.Probe(header, f.Name()) {
			continue
		}
		newFS, err := probe.Enter(header, fullPath)
		if err != nil {
			p.reportErr(err)
			return
		}
		p.stack = append(p.stack, vfsEntry{
			fs:        p.fs,
			path:      p.path,
			cursor:    p.cursor,
			offset:    p.offset,
			entryPath: fullPath,
		})
		p.fs = newFS
		p.path = ""
		p.cursor = 0
		p.offset = 0
		p.loadDir()
		return
	}
}

func (p *Panel) goUp() {
	// Check if at root of current VFS.
	atRoot := p.path == "" || filepath.Dir(p.path) == p.path

	if atRoot && len(p.stack) > 0 {
		// Leave nested VFS, restore previous context.
		p.fs.Leave()
		prev := p.stack[len(p.stack)-1]
		p.stack = p.stack[:len(p.stack)-1]
		p.fs = prev.fs
		p.path = prev.path
		p.cursor = prev.cursor
		p.offset = prev.offset
		p.loadDir()
		return
	}

	if atRoot {
		return
	}

	oldDir := filepath.Base(p.path)
	parent := filepath.Dir(p.path)
	if parent == "." {
		parent = ""
	}
	p.path = parent
	p.loadDir()
	p.cursor = 0
	p.offset = 0
	for i, f := range p.files {
		if f.Name() == oldDir {
			p.cursor = i
			break
		}
	}
}

func (p *Panel) moveTo(idx int) {
	if len(p.files) == 0 {
		p.cursor = 0
		return
	}
	if idx < 0 {
		idx = 0
	}
	if idx >= len(p.files) {
		idx = len(p.files) - 1
	}
	p.cursor = idx
}

func (p *Panel) adjustOffset(visibleRows int) {
	if visibleRows <= 0 {
		return
	}
	if p.cursor < p.offset {
		p.offset = p.cursor
	}
	if p.cursor >= p.offset+visibleRows {
		p.offset = p.cursor - visibleRows + 1
	}
}

// diskPath returns the absolute disk path for a file in the current panel.
// Returns empty string if inside a non-local VFS (e.g., tar within tar).
func (p *Panel) diskPath(name string) string {
	if _, ok := p.fs.(*vfs.LocalFS); ok {
		return filepath.Join(p.path, name)
	}
	return ""
}

// displayPath returns the full path for the title bar, including VFS entry points.
func (p *Panel) displayPath() string {
	if len(p.stack) == 0 {
		return p.path
	}
	base := p.stack[len(p.stack)-1].entryPath
	if p.path == "" {
		return base
	}
	return base + "/" + p.path
}

func (p *Panel) selectedFile() *vfs.File {
	if p.cursor < len(p.files) {
		f := p.files[p.cursor]
		return &f
	}
	return nil
}

// --- App ---

// App is the main application state.
type App struct {
	screen      tcell.Screen
	panels      [2]*Panel
	active      int  // 0 = left, 1 = right
	escMode     bool // true after ESC pressed, next key is treated as Meta
	cmdMode     bool // true when command line is focused
	cmdLine     []rune
	cmdCursor   int
	searchMode  bool // true when incremental search is active
	searchQuery []rune
	copyMode    int    // 0=off, 1=editing source, 2=editing dest
	copyFrom    string // locked source after phase 1
	copyEdit      []rune
	copyCursor    int
	copyHistory   [2][]string // [0]=source history, [1]=dest history
	copyHistIdx   int         // -1 = custom text, >=0 = selected history item
	copyEditSaved []rune      // saved edit text before history navigation
	errMsg        string
}

var (
	styleCmdLine = tcell.StyleDefault.Background(tcell.ColorBlack).Foreground(tcell.ColorWhite)
	styleErr     = tcell.StyleDefault.Background(tcell.ColorBlack).Foreground(tcell.ColorRed)
)

func (a *App) draw() {
	a.screen.Clear()
	w, h := a.screen.Size()

	panelW := w / 2
	panelH := h - 3 // reserve 3 rows: status, command, error

	a.drawPanel(0, 0, panelW, panelH, a.panels[0], a.active == 0)
	a.drawPanel(panelW, 0, w-panelW, panelH, a.panels[1], a.active == 1)
	a.drawStatusLine(0, h-3, w)
	a.drawCmdLine(0, h-2, w)
	a.drawErrLine(0, h-1, w)

	if a.copyMode > 0 {
		a.drawCopyHistory(w, h)
	}

	if !a.cmdMode && !a.searchMode && a.copyMode == 0 {
		a.screen.HideCursor()
	}
}

func (a *App) drawPanel(x, y, w, h int, p *Panel, active bool) {
	if w < 4 || h < 3 {
		return
	}

	innerW := w - 2
	visibleRows := h - 2
	p.adjustOffset(visibleRows)

	// Top border.
	a.setCell(x, y, tcell.RuneULCorner, styleBorder)
	a.setCell(x+w-1, y, tcell.RuneURCorner, styleBorder)

	// Path in title bar: ┌─ path ─────┐
	displayPath := shortenHome(p.displayPath())
	title := []rune(" " + displayPath + " ")
	for i := 1; i < w-1; i++ {
		idx := i - 1
		if idx < len(title) {
			a.setCell(x+i, y, title[idx], styleBorder)
		} else {
			a.setCell(x+i, y, tcell.RuneHLine, styleBorder)
		}
	}

	// File rows.
	for row := 0; row < visibleRows; row++ {
		fileIdx := p.offset + row
		rowY := y + 1 + row

		a.setCell(x, rowY, tcell.RuneVLine, styleBorder)
		a.setCell(x+w-1, rowY, tcell.RuneVLine, styleBorder)

		if fileIdx < len(p.files) {
			f := p.files[fileIdx]
			dirSize := int64(-1)
			if ds, ok := p.dirSizes[f.Name()]; ok {
				dirSize = ds
			}
			line := f.Render(innerW, dirSize)

			var style tcell.Style
			tagged := p.tagged[f.Name()]
			if fileIdx == p.cursor && active {
				style = styleCursor
			} else if tagged {
				style = styleTagged
			} else if fileIdx == p.cursor {
				style = styleDef
			} else if f.IsDir() {
				style = styleDir
			} else {
				style = styleDef
			}

			a.drawString(x+1, rowY, line, innerW, style)
		} else {
			a.drawString(x+1, rowY, "", innerW, styleDef)
		}
	}

	// Bottom border: [n/total]───selected XXX───┘
	bottomY := y + h - 1
	counter := fmt.Sprintf("[%d/%d]", p.cursor, len(p.files))

	// Compute "selected XXX" suffix for tagged items.
	var suffix string
	if len(p.tagged) > 0 {
		var total int64
		for _, f := range p.files {
			if !p.tagged[f.Name()] {
				continue
			}
			if f.IsDir() {
				if ds, ok := p.dirSizes[f.Name()]; ok {
					total += ds
				}
			} else {
				total += f.Size()
			}
		}
		suffix = fmt.Sprintf(" selected %s ", vfs.FormatSize(total))
	}

	suffixStart := w - 1 - len(suffix) // position suffix right before corner
	for i := 0; i < w; i++ {
		switch {
		case i < len(counter):
			a.setCell(x+i, bottomY, rune(counter[i]), styleBorder)
		case suffix != "" && i >= suffixStart && i < suffixStart+len(suffix):
			a.setCell(x+i, bottomY, rune(suffix[i-suffixStart]), styleBorder)
		case i == w-1:
			a.setCell(x+i, bottomY, tcell.RuneLRCorner, styleBorder)
		default:
			a.setCell(x+i, bottomY, tcell.RuneHLine, styleBorder)
		}
	}
}

func (a *App) drawStatusLine(x, y, w int) {
	p := a.panels[a.active]
	f := p.selectedFile()
	if f == nil {
		a.drawString(x, y, "", w, styleStatus)
		return
	}

	var parts []string

	// Disk stats (only for local FS).
	if diskPath := p.diskPath(f.Name()); diskPath != "" {
		var statfs syscall.Statfs_t
		if err := syscall.Statfs(p.path, &statfs); err == nil {
			total := statfs.Blocks * uint64(statfs.Bsize)
			free := statfs.Bavail * uint64(statfs.Bsize)
			usedPct := 0.0
			if total > 0 {
				usedPct = float64(total-free) / float64(total) * 100
			}
			parts = append(parts, fmt.Sprintf("%s free %.1f%% used", vfs.FormatSize(int64(free)), usedPct))
		}

		// Full file details from disk.
		if info, err := os.Lstat(diskPath); err == nil {
			mode := info.Mode().String()
			var nlinks uint64

			if sys, ok := info.Sys().(*syscall.Stat_t); ok {
				nlinks = uint64(sys.Nlink)
			}

			parts = append(parts, fmt.Sprintf("%s %d %d %s %s",
				mode, nlinks, info.Size(),
				info.ModTime().Format("2006-01-02 15:04:05"),
				f.Name(),
			))
		}
	} else {
		// Inside a virtual FS — show basic info from the VFS entry.
		typeStr := "file"
		if f.IsDir() {
			typeStr = "dir"
		}
		parts = append(parts, fmt.Sprintf("%s %s %s %s",
			typeStr, vfs.FormatSize(f.Size()),
			f.ModTime().Format("2006-01-02 15:04:05 -0700"),
			f.Name(),
		))
	}

	a.drawString(x, y, strings.Join(parts, " "), w, styleStatus)
}

func (a *App) drawCmdLine(x, y, w int) {
	if a.copyMode > 0 {
		var prompt string
		if a.copyMode == 1 {
			prompt = "Copy from "
		} else {
			prompt = "Copy from " + a.copyFrom + " to "
		}
		promptW := len([]rune(prompt))
		a.drawString(x, y, prompt, promptW, styleCmdLine)
		editW := w - promptW
		text := string(a.copyEdit)
		a.drawString(x+promptW, y, text, editW, styleCmdLine)
		a.screen.ShowCursor(x+promptW+a.copyCursor, y)
		return
	}

	if a.searchMode {
		const prompt = "? "
		const promptW = 2
		a.drawString(x, y, prompt, promptW, styleCmdLine)
		editW := w - promptW
		text := string(a.searchQuery)
		a.drawString(x+promptW, y, text, editW, styleCmdLine)
		a.screen.ShowCursor(x+promptW+len(a.searchQuery), y)
		return
	}

	if !a.cmdMode {
		a.drawString(x, y, "", w, styleCmdLine)
		return
	}

	const prompt = "> "
	const promptW = 2
	a.drawString(x, y, prompt, promptW, styleCmdLine)
	editW := w - promptW

	// Compute visible window that keeps cursor on screen.
	viewOffset := 0
	if a.cmdCursor > editW-1 {
		viewOffset = a.cmdCursor - editW + 1
	}
	end := viewOffset + editW
	if end > len(a.cmdLine) {
		end = len(a.cmdLine)
	}
	visible := string(a.cmdLine[viewOffset:end])
	a.drawString(x+promptW, y, visible, editW, styleCmdLine)

	a.screen.ShowCursor(x+promptW+a.cmdCursor-viewOffset, y)
}

func (a *App) drawErrLine(x, y, w int) {
	a.drawString(x, y, a.errMsg, w, styleErr)
}

func (a *App) setError(msg string) {
	a.errMsg = msg
}

func (a *App) filteredCopyHistory() []string {
	idx := a.copyMode - 1 // 0=src, 1=dst
	if idx < 0 || idx > 1 {
		return nil
	}
	// Use saved text for filtering when navigating history.
	query := string(a.copyEdit)
	if a.copyHistIdx >= 0 {
		query = string(a.copyEditSaved)
	}
	query = strings.ToLower(query)
	var matches []string
	for _, h := range a.copyHistory[idx] {
		if query == "" || strings.Contains(strings.ToLower(h), query) {
			matches = append(matches, h)
			if len(matches) >= 5 {
				break
			}
		}
	}
	return matches
}

func (a *App) drawCopyHistory(screenW, screenH int) {
	items := a.filteredCopyHistory()
	if len(items) == 0 {
		return
	}
	cmdY := screenH - 2
	for i, item := range items {
		rowY := cmdY - len(items) + i
		if rowY < 0 {
			continue
		}
		style := styleCmdLine
		if i == a.copyHistIdx {
			style = styleCursor
		}
		a.drawString(0, rowY, " "+item, screenW, style)
	}
}

func (a *App) addCopyHistory(idx int, val string) {
	// Remove duplicates.
	hist := a.copyHistory[idx]
	var filtered []string
	for _, h := range hist {
		if h != val {
			filtered = append(filtered, h)
		}
	}
	a.copyHistory[idx] = append([]string{val}, filtered...)
	if len(a.copyHistory[idx]) > 20 {
		a.copyHistory[idx] = a.copyHistory[idx][:20]
	}
}

func (a *App) handleCopyKey(ev *tcell.EventKey) {
	if ev.Key() == tcell.KeyEscape {
		a.copyMode = 0
		return
	}

	if ev.Key() == tcell.KeyEnter {
		if a.copyMode == 1 {
			a.copyFrom = string(a.copyEdit)
			a.addCopyHistory(0, a.copyFrom)
			// Init dest with other panel's path.
			other := a.panels[1-a.active]
			dest := other.path
			a.copyEdit = []rune(dest)
			a.copyCursor = len(a.copyEdit)
			a.copyHistIdx = -1
			a.copyMode = 2
		} else {
			dest := string(a.copyEdit)
			a.addCopyHistory(1, dest)
			a.copyMode = 0
			a.doCopy(a.copyFrom, dest)
		}
		return
	}

	switch ev.Key() {
	case tcell.KeyUp:
		items := a.filteredCopyHistory()
		if len(items) == 0 {
			return
		}
		if a.copyHistIdx < 0 {
			a.copyEditSaved = append([]rune{}, a.copyEdit...)
			a.copyHistIdx = len(items) - 1
		} else if a.copyHistIdx > 0 {
			a.copyHistIdx--
		}
		a.copyEdit = []rune(items[a.copyHistIdx])
		a.copyCursor = len(a.copyEdit)
	case tcell.KeyDown:
		items := a.filteredCopyHistory()
		if a.copyHistIdx < 0 {
			return
		}
		if a.copyHistIdx < len(items)-1 {
			a.copyHistIdx++
			a.copyEdit = []rune(items[a.copyHistIdx])
			a.copyCursor = len(a.copyEdit)
		} else {
			// Back to custom text.
			a.copyHistIdx = -1
			a.copyEdit = a.copyEditSaved
			a.copyCursor = len(a.copyEdit)
		}
	case tcell.KeyBackspace, tcell.KeyBackspace2:
		a.copyHistIdx = -1
		if a.copyCursor > 0 {
			a.copyEdit = append(a.copyEdit[:a.copyCursor-1], a.copyEdit[a.copyCursor:]...)
			a.copyCursor--
		}
	case tcell.KeyLeft:
		if a.copyCursor > 0 {
			a.copyCursor--
		}
	case tcell.KeyRight:
		if a.copyCursor < len(a.copyEdit) {
			a.copyCursor++
		}
	case tcell.KeyCtrlA:
		a.copyCursor = 0
	case tcell.KeyCtrlE:
		a.copyCursor = len(a.copyEdit)
	case tcell.KeyCtrlU:
		a.copyHistIdx = -1
		a.copyEdit = a.copyEdit[a.copyCursor:]
		a.copyCursor = 0
	case tcell.KeyCtrlK:
		a.copyHistIdx = -1
		a.copyEdit = a.copyEdit[:a.copyCursor]
	case tcell.KeyRune:
		a.copyHistIdx = -1
		a.copyEdit = append(a.copyEdit[:a.copyCursor], append([]rune{ev.Rune()}, a.copyEdit[a.copyCursor:]...)...)
		a.copyCursor++
	}
}

func (a *App) doCopy(src, dest string) {
	srcPanel := a.panels[a.active]
	dstPanel := a.panels[1-a.active]

	// Build source path within the source VFS.
	srcPath := src
	if srcPanel.path != "" {
		srcPath = srcPanel.path + "/" + src
	}
	// For local FS, use proper filepath.Join.
	if _, ok := srcPanel.fs.(*vfs.LocalFS); ok {
		srcPath = filepath.Join(srcPanel.path, src)
	}

	// If dest ends with "/" or is a local directory, append source filename.
	if strings.HasSuffix(dest, "/") {
		dest += filepath.Base(src)
	} else if _, ok := dstPanel.fs.(*vfs.LocalFS); ok {
		if info, err := os.Stat(dest); err == nil && info.IsDir() {
			dest = filepath.Join(dest, filepath.Base(src))
		}
	}

	slog.Info("copy", "from", srcPath, "to", dest)

	in, err := srcPanel.fs.ReadFile(srcPath)
	if err != nil {
		a.setError(err.Error())
		return
	}
	defer in.Close()

	if err := dstPanel.fs.WriteFile(dest, in); err != nil {
		a.setError(err.Error())
		return
	}

	// Reload both panels.
	a.panels[0].reload()
	a.panels[1].reload()
}

func (a *App) resumeScreen() {
	slog.Info("resumeScreen: creating new screen")
	newScreen, err := tcell.NewScreen()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error creating screen: %v\n", err)
		os.Exit(1)
	}
	if err := newScreen.Init(); err != nil {
		fmt.Fprintf(os.Stderr, "Error initializing screen: %v\n", err)
		os.Exit(1)
	}
	a.screen = newScreen
	slog.Info("resumeScreen: done")
}

// runShellCmd runs a command in the active panel's directory.
// If fireAndForget is true, the command runs directly (for interactive programs).
// Otherwise, output is piped through less.
func (a *App) runShellCmd(cmd string, fireAndForget bool) {
	p := a.panels[a.active]
	userShell := os.Getenv("SHELL")
	if userShell == "" {
		userShell = "sh"
	}

	slog.Info("runShellCmd", "cmd", cmd, "fireAndForget", fireAndForget)
	a.errMsg = ""
	a.screen.Fini()

	var shell string
	if fireAndForget {
		shell = fmt.Sprintf("cd %s && %s", shellQuote(p.path), cmd)
	} else {
		shell = fmt.Sprintf("cd %s && { %s; } 2>&1 | less", shellQuote(p.path), cmd)
	}

	c := exec.Command(userShell, "-c", shell)
	c.Stdin = os.Stdin
	c.Stdout = os.Stdout
	c.Stderr = os.Stderr
	if err := c.Run(); err != nil {
		a.errMsg = err.Error()
	}

	a.resumeScreen()
	p.reload()
}

func (a *App) execCommand() {
	cmd := strings.TrimSpace(string(a.cmdLine))
	if cmd == "" {
		return
	}

	a.cmdMode = false
	a.cmdLine = nil
	a.cmdCursor = 0

	// Commands ending with "&" or starting with interactive programs run directly.
	fireAndForget := strings.HasSuffix(cmd, "&")
	if fireAndForget {
		cmd = strings.TrimSpace(strings.TrimSuffix(cmd, "&"))
	} else {
		fireAndForget = isInteractiveCmd(cmd)
	}

	a.runShellCmd(cmd, fireAndForget)
}

func (p *Panel) scroll(delta int) {
	p.offset += delta
	max := len(p.files) - 1
	if p.offset < 0 {
		p.offset = 0
	}
	if p.offset > max {
		p.offset = max
	}
	// Keep cursor visible — adjustOffset will clamp it on next draw,
	// but we also nudge the cursor to stay in the visible range.
	p.moveTo(p.cursor + delta)
}

func (a *App) calcSelectedDirSizes() {
	p := a.panels[a.active]
	if p.dirSizes == nil {
		p.dirSizes = make(map[string]int64)
	}

	calcFor := func(f *vfs.File) {
		if f == nil || !f.IsDir() || f.Name() == ".." {
			return
		}
		dp := p.diskPath(f.Name())
		if dp == "" {
			return
		}
		p.dirSizes[f.Name()] = calcDirSize(dp)
	}

	if len(p.tagged) > 0 {
		for _, f := range p.files {
			if p.tagged[f.Name()] {
				calcFor(&f)
			}
		}
	} else {
		calcFor(p.selectedFile())
	}
}

func (p *Panel) reload() {
	cur := p.cursor
	off := p.offset
	p.loadDir()
	p.moveTo(cur)
	p.offset = off
}

func (a *App) handleMeta(ev *tcell.EventKey) {
	p := a.panels[a.active]
	_, h := a.screen.Size()
	pageSize := h - 3
	if pageSize < 1 {
		pageSize = 1
	}

	if ev.Key() == tcell.KeyRune {
		switch ev.Rune() {
		case 'v':
			p.moveTo(p.cursor - pageSize)
		case 'n':
			p.scroll(1)
		case 'p':
			p.scroll(-1)
		case '<':
			p.moveTo(0)
		case '>':
			p.moveTo(len(p.files) - 1)
		}
	}
}

func (a *App) cmdInsertString(s string) {
	runes := []rune(s)
	a.cmdLine = append(a.cmdLine[:a.cmdCursor], append(runes, a.cmdLine[a.cmdCursor:]...)...)
	a.cmdCursor += len(runes)
}

func (a *App) handleCmdKey(ev *tcell.EventKey) {
	p := a.panels[a.active]

	if ev.Key() == tcell.KeyEscape {
		if len(a.cmdLine) == 0 {
			a.cmdMode = false
			return
		}
		a.escMode = true
		return
	}

	if a.escMode {
		a.escMode = false
		if ev.Key() == tcell.KeyEnter {
			if len(p.tagged) > 0 {
				// Insert all tagged filenames.
				var names []string
				for _, f := range p.files {
					if p.tagged[f.Name()] {
						names = append(names, f.Name())
					}
				}
				a.cmdInsertString(strings.Join(names, " "))
			} else if f := p.selectedFile(); f != nil && f.Name() != ".." {
				a.cmdInsertString(f.Name())
			}
			return
		}
		return
	}

	switch ev.Key() {
	case tcell.KeyEnter:
		a.execCommand()
	case tcell.KeyTab:
		// Insert selected filename at cursor.
		if f := p.selectedFile(); f != nil && f.Name() != ".." {
			a.cmdInsertString(f.Name())
		}
	case tcell.KeyUp:
		p.moveTo(p.cursor - 1)
	case tcell.KeyDown:
		p.moveTo(p.cursor + 1)
	case tcell.KeyBackspace, tcell.KeyBackspace2:
		if a.cmdCursor > 0 {
			a.cmdLine = append(a.cmdLine[:a.cmdCursor-1], a.cmdLine[a.cmdCursor:]...)
			a.cmdCursor--
		}
	case tcell.KeyLeft:
		if a.cmdCursor > 0 {
			a.cmdCursor--
		}
	case tcell.KeyRight:
		if a.cmdCursor < len(a.cmdLine) {
			a.cmdCursor++
		}
	case tcell.KeyCtrlA:
		a.cmdCursor = 0
	case tcell.KeyCtrlE:
		a.cmdCursor = len(a.cmdLine)
	case tcell.KeyCtrlU:
		a.cmdLine = a.cmdLine[a.cmdCursor:]
		a.cmdCursor = 0
	case tcell.KeyCtrlK:
		a.cmdLine = a.cmdLine[:a.cmdCursor]
	case tcell.KeyRune:
		a.cmdInsertString(string(ev.Rune()))
	}
}

func (a *App) searchNavigate() {
	p := a.panels[a.active]
	prefix := strings.ToLower(string(a.searchQuery))
	if prefix == "" {
		return
	}
	for i, f := range p.files {
		if strings.HasPrefix(strings.ToLower(f.Name()), prefix) {
			p.moveTo(i)
			return
		}
	}
}

func (a *App) handleSearchKey(ev *tcell.EventKey) {
	switch ev.Key() {
	case tcell.KeyEscape:
		a.searchMode = false
		a.searchQuery = nil
	case tcell.KeyEnter:
		a.searchMode = false
		a.searchQuery = nil
		p := a.panels[a.active]
		if f := p.selectedFile(); f != nil && f.Name() != ".." {
			a.cmdMode = true
			a.cmdLine = []rune(f.Name())
			a.cmdCursor = len(a.cmdLine)
		}
	case tcell.KeyBackspace, tcell.KeyBackspace2:
		if len(a.searchQuery) > 0 {
			a.searchQuery = a.searchQuery[:len(a.searchQuery)-1]
			a.searchNavigate()
		}
	case tcell.KeyRune:
		a.searchQuery = append(a.searchQuery, ev.Rune())
		a.searchNavigate()
	}
}

func (a *App) handleKey(ev *tcell.EventKey) {
	if a.copyMode > 0 {
		a.handleCopyKey(ev)
		return
	}

	if a.searchMode {
		a.handleSearchKey(ev)
		return
	}

	if a.cmdMode {
		a.handleCmdKey(ev)
		return
	}

	// ESC sets meta mode; next key is dispatched as Meta.
	if ev.Key() == tcell.KeyEscape {
		a.escMode = true
		return
	}

	if a.escMode {
		a.escMode = false
		a.handleMeta(ev)
		return
	}

	// Also support real Alt modifier from the terminal.
	if ev.Modifiers()&tcell.ModAlt != 0 {
		a.handleMeta(ev)
		return
	}

	p := a.panels[a.active]
	_, h := a.screen.Size()
	pageSize := h - 3
	if pageSize < 1 {
		pageSize = 1
	}
	halfPage := pageSize / 2
	if halfPage < 1 {
		halfPage = 1
	}

	switch ev.Key() {
	case tcell.KeyUp:
		p.moveTo(p.cursor - 1)
	case tcell.KeyDown:
		p.moveTo(p.cursor + 1)
	case tcell.KeyLeft:
		p.moveTo(p.cursor - pageSize)
	case tcell.KeyRight:
		p.moveTo(p.cursor + pageSize)
	case tcell.KeyEnter:
		p.enter()
	case tcell.KeyTab:
		a.active = 1 - a.active
	case tcell.KeyBackspace, tcell.KeyBackspace2:
		p.goUp()
	case tcell.KeyPgUp:
		p.moveTo(p.cursor - pageSize)
	case tcell.KeyPgDn:
		p.moveTo(p.cursor + pageSize)
	case tcell.KeyHome:
		p.moveTo(0)
	case tcell.KeyEnd:
		p.moveTo(len(p.files) - 1)
	case tcell.KeyCtrlN:
		p.moveTo(p.cursor + 1)
	case tcell.KeyCtrlP:
		p.moveTo(p.cursor - 1)
	case tcell.KeyCtrlA:
		p.moveTo(0)
	case tcell.KeyCtrlE:
		p.moveTo(len(p.files) - 1)
	case tcell.KeyCtrlD:
		p.moveTo(p.cursor + halfPage)
	case tcell.KeyCtrlU:
		p.moveTo(p.cursor - halfPage)
	case tcell.KeyCtrlV:
		p.moveTo(p.cursor + pageSize)
	case tcell.KeyCtrlL:
		p.reload()
	case tcell.KeyRune:
		switch ev.Rune() {
		case 'q':
			a.screen.Fini()
			os.Exit(0)
		case 'k':
			p.moveTo(p.cursor - 1)
		case 'j':
			p.moveTo(p.cursor + 1)
		case 'h':
			a.active = 0
		case 'l':
			a.active = 1
		case '^':
			p.moveTo(0)
		case 'G':
			p.moveTo(len(p.files) - 1)
		case 'x':
			a.cmdMode = true
			a.cmdLine = nil
			a.cmdCursor = 0
		case 's':
			a.searchMode = true
			a.searchQuery = nil
		case ' ':
			if f := p.selectedFile(); f != nil && f.Name() != ".." {
				if p.tagged == nil {
					p.tagged = make(map[string]bool)
				}
				p.tagged[f.Name()] = true
				p.moveTo(p.cursor + 1)
			}
		case '-':
			if f := p.selectedFile(); f != nil {
				delete(p.tagged, f.Name())
				p.moveTo(p.cursor + 1)
			}
		case 'i':
			a.calcSelectedDirSizes()
		case '+':
			p.tagged = make(map[string]bool)
			for _, f := range p.files {
				if f.Name() != ".." {
					p.tagged[f.Name()] = true
				}
			}
		case '_':
			p.tagged = nil
		case 'c':
			if f := p.selectedFile(); f != nil && !f.IsDir() && f.Name() != ".." {
				a.copyMode = 1
				a.copyEdit = []rune(f.Name())
				a.copyCursor = len(a.copyEdit)
				a.copyHistIdx = -1
			}
		}
	}
}

// --- Helpers ---

func calcDirSize(path string) int64 {
	var total int64
	filepath.WalkDir(path, func(_ string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if !d.IsDir() {
			if info, err := d.Info(); err == nil {
				total += info.Size()
			}
		}
		return nil
	})
	return total
}

// interactiveCmds are programs that manage their own terminal I/O.
var interactiveCmds = []string{"vi", "vim", "nano", "less", "more"}

func isInteractiveCmd(cmd string) bool {
	first := strings.Fields(cmd)
	if len(first) == 0 {
		return false
	}
	base := filepath.Base(first[0])
	for _, ic := range interactiveCmds {
		if base == ic {
			return true
		}
	}
	return false
}

func (a *App) setCell(x, y int, ch rune, style tcell.Style) {
	a.screen.SetContent(x, y, ch, nil, style)
}

func (a *App) drawString(x, y int, s string, maxW int, style tcell.Style) {
	runes := []rune(s)
	for i := 0; i < maxW; i++ {
		if i < len(runes) {
			a.screen.SetContent(x+i, y, runes[i], nil, style)
		} else {
			a.screen.SetContent(x+i, y, ' ', nil, style)
		}
	}
}

func readHeader(path string, n int) []byte {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	buf := make([]byte, n)
	n2, _ := f.Read(buf)
	return buf[:n2]
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

func shortenHome(path string) string {
	home, _ := os.UserHomeDir()
	if home != "" && strings.HasPrefix(path, home) {
		return "~" + path[len(home):]
	}
	return path
}

// --- Main ---

func initLogging() {
	f, err := os.OpenFile("xc.log", os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(f, &slog.HandlerOptions{Level: slog.LevelDebug})))
}

func main() {
	initLogging()
	slog.Info("starting xc")

	screen, err := tcell.NewScreen()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
	if err := screen.Init(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
	defer screen.Fini()

	home, _ := os.UserHomeDir()
	if home == "" {
		home = "/"
	}

	cwd, _ := os.Getwd()
	if cwd == "" {
		cwd = home
	}

	localFS := &vfs.LocalFS{}
	probes := []vfs.VFS{&vfs.TarFS{}, &vfs.GCSFS{}, &vfs.S3FS{}}

	app := &App{
		screen: screen,
		active: 0,
	}

	onExec := func(cmd string) {
		app.runShellCmd(cmd, false)
	}

	app.panels = [2]*Panel{
		newPanel(cwd, localFS, probes, app.setError, onExec),
		newPanel(home, localFS, probes, app.setError, onExec),
	}

	for {
		app.draw()
		app.screen.Show()

		ev := app.screen.PollEvent()
		switch ev := ev.(type) {
		case *tcell.EventKey:
			app.handleKey(ev)
		case *tcell.EventResize:
			app.screen.Sync()
		}
	}
}
