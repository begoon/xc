package vfs

import (
	"io"
	"os"
	"path/filepath"
	"sort"
)

// LocalFS implements VFS for the local file system.
type LocalFS struct{}

func (fs *LocalFS) Probe(header []byte, filename string) bool {
	info, err := os.Stat(filename)
	return err == nil && info.IsDir()
}

func (fs *LocalFS) Enter(header []byte, filename string) (VFS, error) {
	return fs, nil
}

func (fs *LocalFS) ReadDir(path string) ([]File, error) {
	entries, err := os.ReadDir(path)
	if err != nil {
		return nil, err
	}

	var files []File
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			continue
		}

		ft := TypeFile
		isSymlink := entry.Type()&os.ModeSymlink != 0
		if isSymlink {
			ft = TypeSymlink
		} else if info.IsDir() {
			ft = TypeDir
		}

		executable := ft == TypeFile && info.Mode()&0o111 != 0
		f := NewFileEx(info.Name(), info.Size(), ft, info.ModTime(), executable)

		if isSymlink {
			if target, err := os.Readlink(filepath.Join(path, info.Name())); err == nil {
				f.linkTarget = target
			}
		}

		files = append(files, f)
	}

	// Sort: directories first, then alphabetical.
	sort.Slice(files, func(i, j int) bool {
		if files[i].IsDir() != files[j].IsDir() {
			return files[i].IsDir()
		}
		return files[i].Name() < files[j].Name()
	})

	return files, nil
}

func (fs *LocalFS) ReadFile(path string) (io.ReadCloser, error) {
	return os.Open(path)
}

func (fs *LocalFS) WriteFile(path string, r io.Reader) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer func() { _ = f.Close() }()
	_, err = io.Copy(f, r)
	return err
}

func (fs *LocalFS) MkdirAll(path string) error {
	return os.MkdirAll(path, 0o755)
}

func (fs *LocalFS) Leave() error {
	return nil
}
