package vfs

import (
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"
)

// FileType represents the type of a file system entry.
type FileType int

const (
	TypeFile FileType = iota
	TypeDir
	TypeSymlink
)

// File represents a file system entry.
type File struct {
	name       string
	size       int64
	fileType   FileType
	modTime    time.Time
	executable bool
	linkTarget string
}

// NewFile creates a new File.
func NewFile(name string, size int64, fileType FileType, modTime time.Time) File {
	return File{name: name, size: size, fileType: fileType, modTime: modTime}
}

// NewFileEx creates a new File with the executable flag.
func NewFileEx(name string, size int64, fileType FileType, modTime time.Time, executable bool) File {
	return File{name: name, size: size, fileType: fileType, modTime: modTime, executable: executable}
}

func (f File) Name() string       { return f.name }
func (f File) Size() int64        { return f.size }
func (f File) Type() FileType     { return f.fileType }
func (f File) ModTime() time.Time { return f.modTime }
func (f File) IsDir() bool        { return f.fileType == TypeDir }
func (f File) IsSymlink() bool    { return f.fileType == TypeSymlink }
func (f File) IsExecutable() bool { return f.executable }
func (f File) LinkTarget() string { return f.linkTarget }

// Ext returns the file extension (e.g., ".txt").
// Returns empty string for directories and dotfiles without additional dots.
func (f File) Ext() string {
	if f.fileType == TypeDir {
		return ""
	}
	name := strings.TrimPrefix(f.name, ".")
	return filepath.Ext(name)
}

// BaseName returns the file name without extension.
func (f File) BaseName() string {
	ext := f.Ext()
	if ext == "" {
		return f.name
	}
	return f.name[:len(f.name)-len(ext)]
}

// FormatSize returns a human-readable size string.
func FormatSize(size int64) string {
	switch {
	case size < 1024:
		return fmt.Sprintf("%d", size)
	case size < 1024*1024:
		return fmt.Sprintf("%.1fk", float64(size)/1024)
	case size < 1024*1024*1024:
		return fmt.Sprintf("%.1fM", float64(size)/(1024*1024))
	default:
		return fmt.Sprintf("%.1fG", float64(size)/(1024*1024*1024))
	}
}

// Render returns a formatted string of the file info for a given width.
// dirSize overrides <DIR> with a human-readable size when >= 0.
func (f File) Render(width int, dirSize int64) string {
	dateStr := f.modTime.Format("06-01-02 15:04")

	const sizeWidth = 6
	nameExtWidth := width - 23 // 1(space) + nameExt + 1(space) + 6(size) + 1(space) + 14(date)
	if nameExtWidth < 1 {
		nameExtWidth = 1
	}

	var nameExt string
	var sizeStr string
	prefix := " "

	switch {
	case f.IsDir():
		if dirSize >= 0 {
			sizeStr = fmt.Sprintf("%*s", sizeWidth, FormatSize(dirSize))
		} else {
			sizeStr = fmt.Sprintf("%*s", sizeWidth, "<DIR>")
		}
		nameExt = padOrTruncate(f.name+"/", nameExtWidth)
	case f.IsSymlink():
		prefix = "@"
		sizeStr = fmt.Sprintf("%*s", sizeWidth, "<LNK>")
		displayName := f.name
		if f.linkTarget != "" {
			displayName = f.name + " -> " + f.linkTarget
		}
		nameExt = padOrTruncate(displayName, nameExtWidth)
	default:
		sizeStr = fmt.Sprintf("%*s", sizeWidth, FormatSize(f.size))
		nameExt = padOrTruncate(f.name, nameExtWidth)
		if f.executable {
			prefix = "*"
		}
	}

	return prefix + nameExt + " " + sizeStr + " " + dateStr
}

func padOrTruncate(s string, width int) string {
	runes := []rune(s)
	if len(runes) > width {
		if width > 1 {
			return string(runes[:width-1]) + "~"
		}
		return string(runes[:width])
	}
	return s + strings.Repeat(" ", width-len(runes))
}

// VFS is the virtual file system interface.
type VFS interface {
	// Probe checks if this VFS can handle the given file.
	Probe(header []byte, filename string) bool
	// Enter enters the VFS for the given file (e.g., open an archive).
	Enter(header []byte, filename string) (VFS, error)
	// ReadDir returns the contents of a directory.
	ReadDir(path string) ([]File, error)
	// ReadFile opens a file for reading.
	ReadFile(path string) (io.ReadCloser, error)
	// WriteFile writes content from r to the given path.
	WriteFile(path string, r io.Reader) error
	// MkdirAll creates a directory and all parents. No-op for cloud VFS.
	MkdirAll(path string) error
	// Leave exits the current VFS context.
	Leave() error
}
