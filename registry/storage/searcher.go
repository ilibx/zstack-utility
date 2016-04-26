package storage

import (
	"bytes"
	"errors"
	"fmt"
	"github.com/docker/distribution/context"
	storagedriver "github.com/docker/distribution/registry/storage/driver"
	"image-store/registry/api/v1"
	"image-store/utils"
	"io"
	"strings"
)

// TODO add caching layer
// Names and tags etc. will be converted to lowercase
type Searcher interface {
	// Returns images found in registry, empty array if not found.
	FindImages(ctx context.Context, name string) ([]*v1.ImageManifest, error)

	// Get the image manifest with a name and reference.
	GetManifest(ctx context.Context, name string, ref string) (*v1.ImageManifest, error)

	// Put a image manifest
	PutManifest(ctx context.Context, name string, ref string, imf *v1.ImageManifest) error

	// List tags under a name
	ListTags(ctx context.Context, name string) ([]string, error)

	// Get the blob json
	GetBlobJsonSpec(name string, digest string) (string, error)

	// Get a read-closer instance
	GetBlobChunkReader(ctx context.Context, name string, tophash string, subhash string, offset int64) (io.ReadCloser, error)

	// Prepare blob upload
	PrepareBlobUpload(ctx context.Context, name string, info *v1.UploadInfo) (string, error)

	// Get uploaded chunk size
	GetUploadedSize(ctx context.Context, name string, uu string) (int64, error)

	// Cancel the upload
	CancelUpload(ctx context.Context, name string, uu string) error

	// Complete the upload
	CompleteUpload(ctx context.Context, name string, uu string) error

	// Get a write-closer instance
	GetChunkWriter(ctx context.Context, name string, uu string) (io.WriteCloser, error)
}

type ImageSearcher struct {
	driver storagedriver.StorageDriver
}

func NewSearcher(d storagedriver.StorageDriver) *ImageSearcher {
	return &ImageSearcher{driver: d}
}

func (ims ImageSearcher) FindImages(ctx context.Context, name string) ([]*v1.ImageManifest, error) {
	return nil, errors.New("not implemented")
}

func getImageJson(ctx context.Context, d storagedriver.StorageDriver, ps string) (*v1.ImageManifest, error) {
	buf, err := d.GetContent(ctx, ps)
	if err != nil {
		return nil, err
	}

	return v1.ParseImageManifest(buf)
}

func (ims ImageSearcher) GetManifest(ctx context.Context, nam string, ref string) (*v1.ImageManifest, error) {
	// If the reference is a tag -
	//  1. get the digest via tag
	//  2. get the manifest via digest
	// Digest can be only first few digests - as long as there is no ambiguity.
	refstr := strings.ToLower(ref)
	name := strings.ToLower(nam)

	if utils.IsDigest(refstr) {
		ps := imageJsonPathSpec{name: name, id: refstr}.pathSpec()
		res, err := ims.driver.List(ctx, ps)
		if err != nil {
			if _, ok := err.(storagedriver.PathNotFoundError); ok {
				return nil, fmt.Errorf("digest not found: %s", ref)
			}
			return nil, err
		}

		switch len(res) {
		case 1:
			return getImageJson(ctx, ims.driver, res[0])
		case 0:
			return nil, fmt.Errorf("internal error - no manifest found")
		default:
			return nil, fmt.Errorf("digest is ambiguous: %s", refstr)
		}
	}

	// ok - it is a tag
	tps := tagPathSpec{name: name, tag: refstr}.pathSpec()
	buf, err := ims.driver.GetContent(ctx, tps)
	if err != nil {
		return nil, err
	}

	idstr := strings.ToLower(strings.TrimSpace(string(buf)))
	if utils.ParseImageId(idstr) == nil {
		return nil, fmt.Errorf("unexpected digest '%s' for tag '%s'", idstr, refstr)
	}

	ips := imageJsonPathSpec{name: name, id: idstr}.pathSpec()
	return getImageJson(ctx, ims.driver, ips)
}

func (ims ImageSearcher) PutManifest(ctx context.Context, nam string, ref string, imf *v1.ImageManifest) error {
	// If the reference is a tag -
	//  1. put the manifest
	//  2. update the tag
	//
	// If the reference is a digest
	//  1. check whether digest matches imf.Id
	//  2. put the manifest
	refstr := strings.ToLower(ref)
	idstr := strings.ToLower(imf.Id)
	name := strings.ToLower(nam)
	isdigest := utils.ParseImageId(refstr) != nil

	if isdigest {
		if refstr != idstr {
			return errors.New("digest and content body mismatch")
		}
	}

	if !imf.Ok() {
		return errors.New("unexpected image manifest format")
	}

	// Check whether its parents exists
	for _, pid := range imf.Parents {
		ps := imageJsonPathSpec{name: name, id: pid}.pathSpec()
		if _, err := ims.driver.Stat(ctx, ps); err != nil {
			return err
		}
	}

	// Check whether the image blob has been uploaded
	bps := blobDigestPathSpec{digest: imf.Blobsum}.pathSpec()
	if _, err := ims.driver.Stat(ctx, bps); err != nil {
		return fmt.Errorf("image blob missing: %s", imf.Blobsum)
	}

	ps := imageJsonPathSpec{name: name, id: idstr}.pathSpec()
	if err := ims.driver.PutContent(ctx, ps, []byte(imf.String())); err != nil {
		return errors.New("failed to update manifest")
	}

	if !isdigest {
		tps := tagPathSpec{name: name, tag: refstr}.pathSpec()
		if err := ims.driver.PutContent(ctx, tps, []byte(idstr)); err != nil {
			return fmt.Errorf("failed to update tag '%s' with digest '%s'", refstr, idstr)
		}
	}

	return nil
}

func (ims ImageSearcher) ListTags(ctx context.Context, name string) ([]string, error) {
	ps := tagsPathSpec{name: name}.pathSpec()

	xs, err := ims.driver.List(ctx, ps)
	if err != nil {
		if _, ok := err.(storagedriver.PathNotFoundError); !ok {
			return nil, err
		}
	}

	res := make([]string, len(xs))
	for i, v := range xs {
		res[i] = strings.TrimPrefix(v, ps+"/")
	}

	return res, nil
}

func (ims ImageSearcher) GetBlobJsonSpec(name string, digest string) (string, error) {
	ps := blobDigestPathSpec{digest: digest}
	if utils.IsBlobDigest(digest) {
		return ps.pathSpec(), nil
	}

	return "", fmt.Errorf("invalid image digest: %s", digest)
}

// Prepare blob upload involves the following steps:
//  1. generate a UUID to identify the upload session
//  2. save the target digest value
//  3. return the target location
func (ims ImageSearcher) PrepareBlobUpload(ctx context.Context, name string, info *v1.UploadInfo) (string, error) {
	uu := utils.NewUUID()
	uips := uploadInfoPathSpec{name: name, id: uu}.pathSpec()
	if err := ims.driver.PutContent(ctx, uips, []byte(info.String())); err != nil {
		return "", err
	}

	urlps := uploadUuidPathSpec{name: name, id: uu}.urlSpec()
	return urlps, nil
}

func (ims ImageSearcher) GetUploadedSize(ctx context.Context, name string, uu string) (int64, error) {
	uups := uploadUuidPathSpec{name: name, id: uu}.pathSpec()

	// List all chunks and check its size
	ls, err := ims.driver.List(ctx, uups)
	if err != nil {
		return 0, err
	}

	var size int64
	for _, fname := range ls {
		if strings.HasPrefix(fname, chunkNamePrefix) {
			info, err := ims.driver.Stat(ctx, fname)
			if err != nil {
				return 0, err
			}

			size += info.Size()
		}
	}

	return size, nil
}

func (ims ImageSearcher) CancelUpload(ctx context.Context, name string, uu string) error {
	uups := uploadUuidPathSpec{name: name, id: uu}.pathSpec()

	_, err := ims.driver.Stat(ctx, uups)
	if err != nil {
		return err
	}

	go ims.driver.Delete(ctx, uups)
	return nil
}

func (ims ImageSearcher) CompleteUpload(ctx context.Context, name string, uu string) error {
	uips := uploadInfoPathSpec{name: name, id: uu}.pathSpec()
	content, err := ims.driver.GetContent(ctx, uips)
	if err != nil {
		return err
	}

	var uploadinfo v1.UploadInfo
	if err = utils.JsonDecode(bytes.NewReader(content), &uploadinfo); err != nil {
		return err
	}

	size, err := ims.GetUploadedSize(ctx, name, uu)
	if err != nil {
		return err
	}

	if size != uploadinfo.Size {
		return fmt.Errorf("blob size (%d) mismatch, expecting %d", size, uploadinfo.Size)
	}

	// TODO move chunks to blobs store and generate  blob json

	uups := uploadUuidPathSpec{name: name, id: uu}.pathSpec()
	ims.driver.Delete(ctx, uups)

	return nil
}

func (ims ImageSearcher) GetChunkWriter(ctx context.Context, name string, uu string, index int) (io.WriteCloser, error) {
	// Check whether the upload UUID exists
	uups := uploadUuidPathSpec{name: name, id: uu}.pathSpec()

	_, err := ims.driver.Stat(ctx, uups)
	if err != nil {
		return nil, err
	}

	// Write the record of started time
	ucps := uploadChunkPathSpec{name: name, id: uu, index: index}.pathSpec()
	return ims.driver.Writer(ctx, ucps, true)
}

func (ims ImageSearcher) GetBlobChunkReader(ctx context.Context, name string, tophash string, subhash string, offset int64) (io.ReadCloser, error) {
	bcps := blobChunkPathSpec{digest: tophash, subhash: subhash}.pathSpec()

	return ims.driver.Reader(ctx, bcps, offset)
}
