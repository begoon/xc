package vfs

import (
	"archive/tar"
	"compress/bzip2"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path"
	"sort"
	"strings"
	"time"
)

// TarFS implements VFS for tar archives (.tar, .tar.gz, .tgz, .tar.bz2, .tbz2).
// As a prober (dirs == nil), it checks filenames. As an instance, it serves directory listings.
type TarFS struct {
	dirs map[string][]File // directory path -> sorted file entries
}

func (t *TarFS) Probe(header []byte, filename string) bool {
	lower := strings.ToLower(filename)
	return strings.HasSuffix(lower, ".tar") ||
		strings.HasSuffix(lower, ".tar.gz") ||
		strings.HasSuffix(lower, ".tgz") ||
		strings.HasSuffix(lower, ".tar.bz2") ||
		strings.HasSuffix(lower, ".tbz2")
}

func (t *TarFS) Enter(header []byte, filename string) (VFS, error) {
	f, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()

	var reader io.Reader = f
	lower := strings.ToLower(filename)
	if strings.HasSuffix(lower, ".gz") || strings.HasSuffix(lower, ".tgz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return nil, err
		}
		defer func() { _ = gz.Close() }()
		reader = gz
	} else if strings.HasSuffix(lower, ".bz2") || strings.HasSuffix(lower, ".tbz2") {
		reader = bzip2.NewReader(f)
	}

	tr := tar.NewReader(reader)
	dirs := make(map[string][]File)
	seen := make(map[string]bool)

	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}

		name := path.Clean(hdr.Name)
		if name == "." || name == "" {
			continue
		}

		dir := path.Dir(name)
		if dir == "." {
			dir = ""
		}
		base := path.Base(name)

		var ft FileType
		switch hdr.Typeflag {
		case tar.TypeDir:
			ft = TypeDir
		case tar.TypeSymlink:
			ft = TypeSymlink
		default:
			ft = TypeFile
		}

		key := dir + "\x00" + base
		if seen[key] {
			continue
		}
		seen[key] = true

		ensureDirChain(dirs, seen, dir)
		dirs[dir] = append(dirs[dir], NewFile(base, hdr.Size, ft, hdr.ModTime))
	}

	for d := range dirs {
		sortFiles(dirs[d])
	}

	return &TarFS{dirs: dirs}, nil
}

func (t *TarFS) ReadDir(p string) ([]File, error) {
	if t.dirs == nil {
		return nil, fmt.Errorf("tar not opened")
	}
	files, ok := t.dirs[p]
	if !ok {
		return nil, fmt.Errorf("directory not found in archive: %s", p)
	}
	return files, nil
}

func (t *TarFS) ReadFile(path string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("reading files from tar archives not supported")
}

func (t *TarFS) WriteFile(path string, r io.Reader) error {
	return fmt.Errorf("writing to tar archives not supported")
}

func (t *TarFS) MkdirAll(path string) error {
	return fmt.Errorf("creating directories in tar archives not supported")
}

func (t *TarFS) Leave() error {
	t.dirs = nil
	return nil
}

// ensureDirChain creates implicit parent directory entries up to the given path.
func ensureDirChain(dirs map[string][]File, seen map[string]bool, dirPath string) {
	if dirPath == "" {
		return
	}

	parent := path.Dir(dirPath)
	if parent == "." {
		parent = ""
	}
	base := path.Base(dirPath)

	key := parent + "\x00" + base
	if seen[key] {
		return
	}
	seen[key] = true

	ensureDirChain(dirs, seen, parent)
	dirs[parent] = append(dirs[parent], NewFile(base, 0, TypeDir, time.Time{}))
}

func sortFiles(files []File) {
	sort.Slice(files, func(i, j int) bool {
		if files[i].IsDir() != files[j].IsDir() {
			return files[i].IsDir()
		}
		return files[i].Name() < files[j].Name()
	})
}
