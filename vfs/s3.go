package vfs

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// S3FS implements VFS for Amazon S3 buckets.
// As a prober (client == nil), it checks .s3 files. As an instance, it serves bucket listings.
type S3FS struct {
	client *s3.Client
	bucket string
}

func (s *S3FS) Probe(header []byte, filename string) bool {
	if !strings.HasSuffix(strings.ToLower(filename), ".s3") {
		return false
	}
	return strings.HasPrefix(string(header), "type=s3")
}

func (s *S3FS) Enter(header []byte, filename string) (VFS, error) {
	f, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var bucket, accessKey, secretKey, region string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		switch {
		case strings.HasPrefix(line, "bucket="):
			bucket = strings.TrimPrefix(line, "bucket=")
			bucket = strings.TrimPrefix(bucket, "s3://")
		case strings.HasPrefix(line, "AWS_ACCESS_KEY_ID="):
			accessKey = strings.TrimPrefix(line, "AWS_ACCESS_KEY_ID=")
		case strings.HasPrefix(line, "AWS_SECRET_ACCESS_KEY="):
			secretKey = strings.TrimPrefix(line, "AWS_SECRET_ACCESS_KEY=")
		case strings.HasPrefix(line, "AWS_REGION="):
			region = strings.TrimPrefix(line, "AWS_REGION=")
		}
	}

	if bucket == "" {
		return nil, fmt.Errorf("no bucket specified in %s", filename)
	}
	if region == "" {
		region = "us-east-1"
	}

	var opts []func(*s3.Options)
	if accessKey != "" && secretKey != "" {
		opts = append(opts, func(o *s3.Options) {
			o.Credentials = credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")
		})
	}
	opts = append(opts, func(o *s3.Options) {
		o.Region = region
	})

	client := s3.New(s3.Options{}, opts...)

	return &S3FS{client: client, bucket: bucket}, nil
}

func (s *S3FS) ReadDir(path string) ([]File, error) {
	if s.client == nil {
		return nil, fmt.Errorf("S3 not connected")
	}

	prefix := path
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}

	ctx := context.Background()
	delimiter := "/"
	input := &s3.ListObjectsV2Input{
		Bucket:    aws.String(s.bucket),
		Prefix:    aws.String(prefix),
		Delimiter: aws.String(delimiter),
	}

	var files []File
	paginator := s3.NewListObjectsV2Paginator(s.client, input)
	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return nil, err
		}

		for _, cp := range page.CommonPrefixes {
			name := strings.TrimPrefix(aws.ToString(cp.Prefix), prefix)
			name = strings.TrimSuffix(name, "/")
			if name != "" {
				files = append(files, NewFile(name, 0, TypeDir, time.Time{}))
			}
		}

		for _, obj := range page.Contents {
			name := strings.TrimPrefix(aws.ToString(obj.Key), prefix)
			if name == "" {
				continue
			}
			modTime := aws.ToTime(obj.LastModified)
			files = append(files, NewFile(name, aws.ToInt64(obj.Size), TypeFile, modTime))
		}
	}

	sortFiles(files)
	return files, nil
}

func (s *S3FS) ReadFile(path string) (io.ReadCloser, error) {
	if s.client == nil {
		return nil, fmt.Errorf("S3 not connected")
	}
	ctx := context.Background()
	out, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(path),
	})
	if err != nil {
		return nil, err
	}
	return out.Body, nil
}

func (s *S3FS) WriteFile(path string, r io.Reader) error {
	if s.client == nil {
		return fmt.Errorf("S3 not connected")
	}

	// Read all into memory for PutObject (S3 needs content length or use upload manager).
	data, err := io.ReadAll(r)
	if err != nil {
		return err
	}

	ctx := context.Background()
	_, err = s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(path),
		Body:   bytes.NewReader(data),
	})
	return err
}

func (s *S3FS) Leave() error {
	s.client = nil
	return nil
}
