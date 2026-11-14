// Copyright (c) 2026 Granary contributors
//
// This file is part of Granary, an unofficial fork derived from MinIO.
// SPDX-License-Identifier: AGPL-3.0-or-later

package cmd

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/xml"
	"fmt"
	"hash/crc32"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/minio/minio-go/v7/pkg/signer"
	"github.com/relvo-labs/granary/internal/auth"
	xhttp "github.com/relvo-labs/granary/internal/http"
)

func unsignedTrailerCRC(data []byte) string {
	var sum [4]byte
	binary.BigEndian.PutUint32(sum[:], crc32.ChecksumIEEE(data))
	return base64.StdEncoding.EncodeToString(sum[:])
}

// Use the real API router and middleware, including auth classification, date
// checks and multiple-auth rejection. The harness runs both storage backends.
func TestUnsignedTrailerUploadAuth(t *testing.T) {
	ExecObjectLayerAPITest(ExecObjectLayerAPITestArgs{t: t, objAPITest: testUnsignedTrailerUploadAuth})
}

func testUnsignedTrailerUploadAuth(obj ObjectLayer, instance, bucket string, router http.Handler, cred auth.Credentials, t *testing.T) {
	ctx := context.Background()
	content := []byte("unsigned trailer authentication regression\n")
	for _, public := range []bool{false, true} {
		if public {
			policy := []byte(fmt.Sprintf(`{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},"Action":["s3:PutObject"],"Resource":["arn:aws:s3:::%s/*"]}]}`, bucket))
			req, err := newTestSignedRequestV4(http.MethodPut, "http://localhost/"+bucket+"?policy", int64(len(policy)), bytes.NewReader(policy), cred.AccessKey, cred.SecretKey, nil)
			if err != nil {
				t.Fatal(err)
			}
			rec := httptest.NewRecorder()
			router.ServeHTTP(rec, req)
			if rec.Code != http.StatusNoContent {
				t.Fatalf("install anonymous upload policy: status %d", rec.Code)
			}
		}
		for _, operation := range []string{"PutObject", "PutObjectPart", "Extract"} {
			for _, mode := range []string{"query-credential", "wrong-signature", "signed", "anonymous", "header-and-query", "presigned-trailer", "raw-tar-query", "raw-tar-wrong-signature", "missing-date", "expired-date", "future-date", "bad-checksum", "presigned", "signed-streaming"} {
				if strings.HasPrefix(mode, "raw-tar") && operation != "Extract" {
					continue
				}
				t.Run(fmt.Sprintf("%s/public=%t/%s/%s", instance, public, operation, mode), func(t *testing.T) {
					object := strings.ToLower(operation + "-" + mode + fmt.Sprint(public))
					storedObject := object
					payload := content
					if operation == "Extract" {
						storedObject = object + "/entry.txt"
						var archive bytes.Buffer
						tw := tar.NewWriter(&archive)
						if err := tw.WriteHeader(&tar.Header{Name: "entry.txt", Mode: 0o600, Size: int64(len(content))}); err != nil {
							t.Fatal(err)
						}
						if _, err := tw.Write(content); err != nil {
							t.Fatal(err)
						}
						if err := tw.Close(); err != nil {
							t.Fatal(err)
						}
						payload = archive.Bytes()
					}
					resource := "http://localhost/" + bucket + "/" + object
					var uploadID string
					if operation == "PutObjectPart" {
						mi, err := obj.NewMultipartUpload(ctx, bucket, object, ObjectOptions{})
						if err != nil {
							t.Fatal(err)
						}
						uploadID = mi.UploadID
						defer obj.AbortMultipartUpload(ctx, bucket, object, uploadID, ObjectOptions{})
						resource += "?" + url.Values{"uploadId": {uploadID}, "partNumber": {"1"}}.Encode()
					}
					req, err := http.NewRequest(http.MethodPut, resource, bytes.NewReader(payload))
					if err != nil {
						t.Fatal(err)
					}
					if operation == "Extract" {
						req.Header.Set(xhttp.AmzSnowballExtract, "true")
						req.Header.Set(xhttp.MinIOSnowballPrefix, object)
					}
					checksum := unsignedTrailerCRC(payload)
					if mode == "bad-checksum" {
						checksum = unsignedTrailerCRC([]byte("different payload"))
					}
					req.Header.Set(xhttp.AmzContentSha256, unsignedPayloadTrailer)
					trailer := http.Header{"X-Amz-Checksum-Crc32": {checksum}}
					if mode == "anonymous" || mode == "query-credential" || mode == "raw-tar-query" {
						req = signer.UnsignedTrailer(*req, trailer)
					} else {
						secret := cred.SecretKey
						if mode == "wrong-signature" || mode == "raw-tar-wrong-signature" {
							secret += "-incorrect"
						}
						req = signer.SignV4Trailer(*req, cred.AccessKey, secret, "", globalSite.Region(), trailer)
					}
					if mode == "query-credential" || mode == "header-and-query" || mode == "raw-tar-query" {
						query := req.URL.Query()
						query.Set(xhttp.AmzCredential, getCredentialString(cred.AccessKey, globalSite.Region(), UTCNow()))
						req.URL.RawQuery = query.Encode()
					}
					if strings.HasPrefix(mode, "raw-tar") {
						// The old Extract handler also accepted an unframed tar with
						// a fabricated unsigned-trailer signature and wrote its entries.
						req.Body = io.NopCloser(bytes.NewReader(payload))
						req.ContentLength = int64(len(payload))
					}
					if mode == "presigned-trailer" {
						req.Header.Del(xhttp.Authorization)
						if err := preSignV4(req, cred.AccessKey, cred.SecretKey, 300); err != nil {
							t.Fatal(err)
						}
					}
					if mode == "missing-date" {
						req.Header.Del(xhttp.AmzDate)
					}
					if mode == "expired-date" || mode == "future-date" {
						delta := -time.Hour
						if mode == "future-date" {
							delta = time.Hour
						}
						req.Header.Del(xhttp.Authorization)
						if _, err := signStreamingRequest(req, cred.AccessKey, cred.SecretKey, UTCNow().Add(delta)); err != nil {
							t.Fatal(err)
						}
					}
					if mode == "presigned" || mode == "signed-streaming" {
						headers := req.Header.Clone()
						if mode == "presigned" {
							req, err = http.NewRequest(http.MethodPut, resource, bytes.NewReader(payload))
							if err == nil {
								if operation == "Extract" {
									req.Header.Set(xhttp.AmzSnowballExtract, "true")
									req.Header.Set(xhttp.MinIOSnowballPrefix, object)
								}
								req = signer.PreSignV4(*req, cred.AccessKey, cred.SecretKey, "", globalSite.Region(), 300)
							}
						} else {
							req, err = newTestStreamingSignedRequest(http.MethodPut, resource, int64(len(payload)), 1024, bytes.NewReader(payload), cred.AccessKey, cred.SecretKey)
						}
						if err != nil {
							t.Fatal(err)
						}
						if operation == "Extract" {
							req.Header.Set(xhttp.AmzSnowballExtract, headers.Get(xhttp.AmzSnowballExtract))
							req.Header.Set(xhttp.MinIOSnowballPrefix, object)
							// Sign after adding the routing header.
							if mode == "signed-streaming" {
								req.Header.Del(xhttp.Authorization)
								now := UTCNow()
								sig, err := signStreamingRequest(req, cred.AccessKey, cred.SecretKey, now)
								if err != nil {
									t.Fatal(err)
								}
								req, err = assembleStreamingChunks(req, bytes.NewReader(payload), 1024, cred.SecretKey, sig, now)
								if err != nil {
									t.Fatal(err)
								}
							}
						}
					}
					rec := httptest.NewRecorder()
					router.ServeHTTP(rec, req)
					want := ErrNone
					switch mode {
					case "query-credential", "raw-tar-query", "presigned-trailer":
						want = ErrSignatureVersionNotSupported
					case "header-and-query":
						want = ErrSignatureVersionNotSupported
					case "wrong-signature", "raw-tar-wrong-signature":
						want = ErrSignatureDoesNotMatch
					case "missing-date":
						want = ErrMissingDateHeader
					case "expired-date", "future-date":
						want = ErrRequestTimeTooSkewed
					case "bad-checksum":
						want = ErrContentChecksumMismatch
					case "anonymous":
						if !public {
							want = ErrAccessDenied
						}
					}
					if want != ErrNone {
						var response APIErrorResponse
						if err := xml.Unmarshal(rec.Body.Bytes(), &response); err != nil {
							t.Errorf("expected auth error XML, got status %d", rec.Code)
						}
						apiErr := errorCodes.ToAPIErr(want)
						if rec.Code != apiErr.HTTPStatusCode || response.Code != apiErr.Code {
							t.Errorf("status/code = %d/%s, want %d/%s", rec.Code, response.Code, apiErr.HTTPStatusCode, apiErr.Code)
						}
						if uploadID != "" {
							parts, err := obj.ListObjectParts(ctx, bucket, object, uploadID, 0, 10, ObjectOptions{})
							if err != nil || len(parts.Parts) != 0 {
								t.Errorf("rejected upload left parts: count=%d, err=%v", len(parts.Parts), err)
							}
						}
						// Extraction is not transactional: an integrity error at the
						// end of the archive can leave already extracted entries.
						if _, err := obj.GetObjectInfo(ctx, bucket, storedObject, ObjectOptions{}); (operation != "Extract" || mode != "bad-checksum") && !isErrObjectNotFound(err) {
							t.Errorf("rejected upload left an object: %v", err)
						}
						return
					}
					if rec.Code != http.StatusOK {
						var response APIErrorResponse
						_ = xml.Unmarshal(rec.Body.Bytes(), &response)
						t.Fatalf("status/code = %d/%s (%s), want 200", rec.Code, response.Code, response.Message)
					}
					if mode == "signed" || mode == "anonymous" {
						if got := rec.Header().Get("X-Amz-Checksum-Crc32"); got != checksum {
							t.Errorf("response checksum = %q, want %q", got, checksum)
						}
					}
					if uploadID != "" {
						parts, err := obj.ListObjectParts(ctx, bucket, object, uploadID, 0, 10, ObjectOptions{})
						if err != nil || len(parts.Parts) != 1 {
							t.Fatalf("uploaded parts: count=%d, err=%v", len(parts.Parts), err)
						}
						if mode == "signed" || mode == "anonymous" {
							if parts.Parts[0].ChecksumCRC32 != checksum {
								t.Errorf("stored part checksum = %q, want %q", parts.Parts[0].ChecksumCRC32, checksum)
							}
						}
						if _, err := obj.CompleteMultipartUpload(ctx, bucket, object, uploadID, []CompletePart{{PartNumber: 1, ETag: parts.Parts[0].ETag}}, ObjectOptions{}); err != nil {
							t.Fatal(err)
						}
					}
					reader, err := obj.GetObjectNInfo(ctx, bucket, storedObject, nil, nil, ObjectOptions{})
					if err != nil {
						t.Fatal(err)
					}
					defer reader.Close()
					got, err := io.ReadAll(reader)
					if err != nil || !bytes.Equal(got, content) {
						t.Errorf("stored content differs: err=%v", err)
					}
				})
			}
		}
	}
}

func TestUnsignedTrailerQueryAuthRejected(t *testing.T) {
	for _, key := range []string{
		"X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires",
		"X-Amz-SignedHeaders", "X-Amz-Signature", "X-Amz-Security-Token",
		"X-Amz-Content-Sha256", "AWSAccessKeyId", "Signature", "Expires",
	} {
		for _, authorization := range []string{"", signV4Algorithm, signV2Algorithm} {
			t.Run(key+"/"+authorization, func(t *testing.T) {
				// Presence, including an empty value, is enough to reject a
				// partial presigned request before credentials are consulted.
				req := httptest.NewRequest(http.MethodPut, "http://localhost/bucket/object?"+url.Values{key: {""}}.Encode(), nil)
				req.Header.Set(xhttp.AmzContentSha256, unsignedPayloadTrailer)
				req.Header.Set(xhttp.Authorization, authorization)
				if got := getRequestAuthType(req); got != authTypeUnknown {
					t.Fatalf("auth type = %v, want unknown", got)
				}
			})
		}
	}
}

func TestTrailerAuthPolicyConditions(t *testing.T) {
	for _, payload := range []string{unsignedPayloadTrailer, streamingContentSHA256Trailer} {
		for _, signed := range []bool{false, true} {
			if !signed && payload != unsignedPayloadTrailer {
				continue
			}
			t.Run(fmt.Sprintf("%s/signed=%t", payload, signed), func(t *testing.T) {
				req := httptest.NewRequest(http.MethodPut, "http://localhost/bucket/object", nil)
				req.Header.Set(xhttp.AmzContentSha256, payload)
				var wantVersion, wantAuth string
				if signed {
					req.Header.Set(xhttp.Authorization, signV4Algorithm)
					wantVersion, wantAuth = signV4Algorithm, "REST-HEADER"
				}
				values := getConditionValues(req, "", auth.AnonymousCredentials)
				if values["signatureversion"][0] != wantVersion || values["authType"][0] != wantAuth {
					t.Errorf("signatureversion/authType = %q/%q, want %q/%q", values["signatureversion"][0], values["authType"][0], wantVersion, wantAuth)
				}
			})
		}
	}
}
