package vfs

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"cloud.google.com/go/storage"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

// GCSFS implements VFS for Google Cloud Storage buckets.
// As a prober (client == nil), it checks .gcs files. As an instance, it serves bucket listings.
type GCSFS struct {
	client *storage.Client
	bucket string
}

func (g *GCSFS) Probe(header []byte, filename string) bool {
	if !strings.HasSuffix(strings.ToLower(filename), ".gcs") {
		return false
	}
	return strings.HasPrefix(string(header), "type=gcs")
}

func (g *GCSFS) Enter(header []byte, filename string) (VFS, error) {
	f, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var bucket, key string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "bucket=") {
			bucket = strings.TrimPrefix(line, "bucket=")
			bucket = strings.TrimPrefix(bucket, "gs://")
		} else if strings.HasPrefix(line, "key=") {
			key = strings.TrimPrefix(line, "key=")
		}
	}

	if bucket == "" {
		return nil, fmt.Errorf("no bucket specified in %s", filename)
	}

	// Resolve key path relative to the .gcs file directory.
	if key != "" && !filepath.IsAbs(key) {
		key = filepath.Join(filepath.Dir(filename), key)
	}

	ctx := context.Background()
	var opts []option.ClientOption
	if key != "" {
		opts = append(opts, option.WithCredentialsFile(key))
	}

	client, err := storage.NewClient(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("creating GCS client: %w", err)
	}

	return &GCSFS{client: client, bucket: bucket}, nil
}

func (g *GCSFS) ReadDir(path string) ([]File, error) {
	if g.client == nil {
		return nil, fmt.Errorf("GCS not connected")
	}

	prefix := path
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}

	ctx := context.Background()
	query := &storage.Query{
		Prefix:    prefix,
		Delimiter: "/",
	}

	var files []File
	it := g.client.Bucket(g.bucket).Objects(ctx, query)
	for {
		attrs, err := it.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			return nil, err
		}

		if attrs.Prefix != "" {
			// Pseudo-directory.
			name := strings.TrimPrefix(attrs.Prefix, prefix)
			name = strings.TrimSuffix(name, "/")
			if name != "" {
				files = append(files, NewFile(name, 0, TypeDir, attrs.Updated))
			}
		} else {
			// Object (file).
			name := strings.TrimPrefix(attrs.Name, prefix)
			if name == "" {
				continue
			}
			files = append(files, NewFile(name, attrs.Size, TypeFile, attrs.Updated))
		}
	}

	sortFiles(files)
	return files, nil
}

func (g *GCSFS) ReadFile(path string) (io.ReadCloser, error) {
	if g.client == nil {
		return nil, fmt.Errorf("GCS not connected")
	}
	ctx := context.Background()
	return g.client.Bucket(g.bucket).Object(path).NewReader(ctx)
}

func (g *GCSFS) WriteFile(path string, r io.Reader) error {
	if g.client == nil {
		return fmt.Errorf("GCS not connected")
	}
	ctx := context.Background()
	w := g.client.Bucket(g.bucket).Object(path).NewWriter(ctx)
	if _, err := io.Copy(w, r); err != nil {
		w.Close()
		return err
	}
	return w.Close()
}

func (g *GCSFS) Leave() error {
	if g.client != nil {
		g.client.Close()
		g.client = nil
	}
	return nil
}
