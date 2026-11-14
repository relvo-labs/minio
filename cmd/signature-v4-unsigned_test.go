// Copyright (c) 2015-2021 MinIO, Inc.
//
// This file is part of MinIO Object Storage stack
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

package cmd

import (
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/relvo-labs/granary/internal/auth"
	xhttp "github.com/relvo-labs/granary/internal/http"
)

// signSelectedV4 signs an explicit header set, independently of the verifier's
// extraction and completeness checks. The content hash is deliberately omitted
// from SignedHeaders to exercise S3's payload-hash exception.
func signSelectedV4(t *testing.T, r *http.Request, presigned bool, payload string, headers http.Header) {
	t.Helper()
	now := UTCNow()
	region := globalSite.Region()
	scope := getScope(now, region)
	headers = headers.Clone()
	headers.Set("Host", r.Host)
	query := r.URL.Query()
	if presigned {
		query.Set(xhttp.AmzAlgorithm, signV4Algorithm)
		query.Set(xhttp.AmzCredential, globalActiveCred.AccessKey+"/"+scope)
		query.Set(xhttp.AmzDate, now.Format(iso8601Format))
		query.Set(xhttp.AmzExpires, "600")
		query.Set(xhttp.AmzSignedHeaders, getSignedHeaders(headers))
	} else {
		headers.Set(xhttp.AmzDate, now.Format(iso8601Format))
		r.Header.Set(xhttp.AmzContentSha256, payload)
	}
	for k, v := range headers {
		if k != "Host" {
			r.Header[k] = v
		}
	}
	canonical := getCanonicalRequest(headers, payload, query.Encode(), r.URL.Path, r.Method)
	sig := getSignature(getSigningKey(globalActiveCred.SecretKey, now, region, serviceS3), getStringToSign(canonical, now, scope))
	if presigned {
		query.Set(xhttp.AmzSignature, sig)
	} else {
		r.Header.Set(xhttp.Authorization, fmt.Sprintf("%s Credential=%s/%s, SignedHeaders=%s, Signature=%s",
			signV4Algorithm, globalActiveCred.AccessKey, scope, getSignedHeaders(headers), sig))
	}
	r.URL.RawQuery = query.Encode()
	if r.Body == nil {
		r.Body = http.NoBody
	}
	if err := r.ParseForm(); err != nil {
		t.Fatal(err)
	}
}

func TestSigV4UnsignedHeaders(t *testing.T) {
	obj, dir, err := prepareFS(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	if err = newTestConfig(globalMinioDefaultRegion, obj); err != nil {
		t.Fatal(err)
	}

	for _, presigned := range []bool{false, true} {
		for _, tc := range []struct {
			name   string
			header string
			values []string
		}{
			{"copy-source", xhttp.AmzCopySource, []string{"/source/secret"}},
			{"empty-first", xhttp.AmzCopySource, []string{"", "/source/secret"}},
			{"empty", "X-Amz-New-Operation", []string{""}},
			{"no-values", "X-Amz-New-Operation", nil},
			{"mixed-case", "x-aMz-NeW-Operation", []string{"value"}},
			{"metadata", "X-Amz-Meta-Test", []string{"value"}},
			{"date", xhttp.AmzDate, []string{UTCNow().Format(iso8601Format)}},
			{"signature", xhttp.AmzSignature, []string{"value"}},
			{"signature-age", "X-Amz-Signature-Age", []string{"0"}},
			{"decoded-length", xhttp.AmzDecodedContentLength, []string{"0"}},
			{"trailer", "X-Amz-Trailer", []string{"x-amz-checksum-crc32"}},
		} {
			if !presigned && tc.header == xhttp.AmzDate {
				continue
			} // Signed by the header signer.
			t.Run(fmt.Sprintf("presigned=%t/%s", presigned, tc.name), func(t *testing.T) {
				r, err := http.NewRequest(http.MethodPut, "http://localhost/bucket/destination", nil)
				if err != nil {
					t.Fatal(err)
				}
				payload := emptySHA256
				if presigned {
					payload = unsignedPayload
				}
				signSelectedV4(t, r, presigned, payload, http.Header{})
				r.Header[tc.header] = tc.values
				if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrUnsignedHeaders {
					t.Errorf("unsigned header: got %s, want %s", niceError(got), niceError(ErrUnsignedHeaders))
				}
			})
		}
	}
}

func TestSigV4HeaderCompatibility(t *testing.T) {
	obj, dir, err := prepareFS(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	if err = newTestConfig(globalMinioDefaultRegion, obj); err != nil {
		t.Fatal(err)
	}

	for _, presigned := range []bool{false, true} {
		for _, method := range []string{http.MethodPut, http.MethodGet} {
			t.Run(fmt.Sprintf("presigned=%t/%s", presigned, method), func(t *testing.T) {
				r, err := http.NewRequest(method, "http://localhost/bucket/object", nil)
				if err != nil {
					t.Fatal(err)
				}
				payload := emptySHA256
				if presigned {
					payload = unsignedPayload
				}
				// A signed copy source, including repeated values, remains valid.
				headers := http.Header{xhttp.AmzCopySource: {"/bucket/source", "/bucket/source"}}
				signSelectedV4(t, r, presigned, payload, headers)
				r.Header.Set(xhttp.AmzContentSha256, payload)
				r.Header.Set("Content-Type", "application/octet-stream")
				r.Header.Set("User-Agent", "unsigned-header-regression")
				for range 2 { // Presigned verification adds internal signature-age state.
					if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrNone {
						t.Fatalf("valid signature: %s", niceError(got))
					}
				}
				r.Header.Set(xhttp.AmzCopySource, "/bucket/different-source")
				if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrSignatureDoesNotMatch {
					t.Fatalf("tampered signed copy source: %s", niceError(got))
				}
				r.Header[xhttp.AmzCopySource] = headers[xhttp.AmzCopySource]
				if presigned {
					if age := getConditionValues(r, "", globalActiveCred)["signatureAge"]; len(age) != 1 {
						t.Fatal("verified presigned signature must expose signatureAge to policy")
					}
					r.Header.Set("X-Amz-Signature-Age", "client-replacement")
					if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrUnsignedHeaders {
						t.Fatalf("replaced internal signature-age: %s", niceError(got))
					}
				} else {
					r.Header.Set(xhttp.AmzContentSha256, unsignedPayload)
					if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrSignatureDoesNotMatch {
						t.Fatalf("tampered unsigned content hash: %s", niceError(got))
					}
				}
			})
		}
	}

	t.Run("temporary-query-credentials", func(t *testing.T) {
		saved := globalActiveCred
		defer func() { globalActiveCred = saved }()
		// Public, test-only credentials; the token is generated locally and never logged.
		globalActiveCred, err = auth.CreateNewCredentialsWithMetadata("unsigned-header-test", "unsigned-header-test-secret",
			map[string]any{"exp": UTCNow().Add(time.Hour).Unix()}, "unsigned-header-test-secret")
		if err != nil {
			t.Fatal(err)
		}
		r, err := http.NewRequest(http.MethodGet, "http://localhost/bucket/object", nil)
		if err != nil {
			t.Fatal(err)
		}
		q := url.Values{}
		q.Set(xhttp.AmzSecurityToken, globalActiveCred.SessionToken)
		q.Set(xhttp.AmzContentSha256, unsignedPayload)
		r.URL.RawQuery = q.Encode()
		signSelectedV4(t, r, true, unsignedPayload, http.Header{})
		if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrNone {
			t.Fatalf("query authentication parameters: %s", niceError(got))
		}
		// A query token does not exempt an additional unsigned token header.
		r.Header.Set(xhttp.AmzSecurityToken, globalActiveCred.SessionToken)
		if got := reqSignatureV4Verify(r, globalSite.Region(), serviceS3); got != ErrUnsignedHeaders {
			t.Fatalf("unsigned token header: %s", niceError(got))
		}
	})

	for _, payload := range []string{streamingContentSHA256, streamingContentSHA256Trailer, unsignedPayloadTrailer} {
		t.Run(payload, func(t *testing.T) {
			r, err := http.NewRequest(http.MethodPut, "http://localhost/bucket/object", nil)
			if err != nil {
				t.Fatal(err)
			}
			headers := http.Header{xhttp.AmzDecodedContentLength: {"0"}}
			if strings.HasSuffix(payload, "TRAILER") {
				headers.Set("X-Amz-Trailer", "x-amz-checksum-crc32")
			}
			signSelectedV4(t, r, false, payload, headers)
			verify := func() APIErrorCode {
				if payload == unsignedPayloadTrailer {
					return doesSignatureMatch(payload, r, globalSite.Region(), serviceS3)
				}
				_, _, _, _, code := calculateSeedSignature(r, payload == streamingContentSHA256Trailer)
				return code
			}
			if got := verify(); got != ErrNone {
				t.Fatalf("signed streaming headers: %s", niceError(got))
			}
			r.Header.Set("X-Amz-Unsigned", "value")
			if got := verify(); got != ErrUnsignedHeaders {
				t.Fatalf("unsigned streaming header: %s", niceError(got))
			}
		})
	}
}
