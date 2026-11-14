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
	"encoding/xml"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/relvo-labs/granary/internal/auth"
	xhttp "github.com/relvo-labs/granary/internal/http"
)

func TestAPIUnsignedCopySource(t *testing.T) {
	ExecObjectLayerAPITest(ExecObjectLayerAPITestArgs{
		t: t, objAPITest: testAPIUnsignedCopySource,
		// Use the production router, including its CopyObject dispatch rule.
	})
}

func testAPIUnsignedCopySource(obj ObjectLayer, instanceType, bucket string, router http.Handler, cred auth.Credentials, t *testing.T) {
	ctx := t.Context()
	source := "secret-source"
	data := "test-only source content"
	if _, err := obj.PutObject(ctx, bucket, source,
		mustGetPutObjReader(t, strings.NewReader(data), int64(len(data)), "", ""), ObjectOptions{UserDefined: map[string]string{
			xhttp.AmzObjectTagging:                              "test=value",
			strings.ToLower(xhttp.AmzObjectLockMode):            "GOVERNANCE",
			strings.ToLower(xhttp.AmzObjectLockRetainUntilDate): UTCNow().Add(time.Hour).Format(time.RFC3339),
		}}); err != nil {
		t.Fatal(err)
	}

	for _, mode := range []string{"presigned-v4", "signed-v4", "presigned-v2", "signed-v2"} {
		t.Run(instanceType+"/"+mode, func(t *testing.T) {
			destination := "destination-" + mode
			req, err := newTestRequest(http.MethodPut, getPutObjectURL("", bucket, destination), 0, nil)
			if err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "presigned-v4":
				req.Header.Del(xhttp.AmzContentSha256)
				err = preSignV4(req, cred.AccessKey, cred.SecretKey, 600)
			case "signed-v4":
				err = signRequestV4(req, cred.AccessKey, cred.SecretKey)
			case "presigned-v2":
				err = preSignV2(req, cred.AccessKey, cred.SecretKey, 600)
			case "signed-v2":
				err = signRequestV2(req, cred.AccessKey, cred.SecretKey)
			}
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set(xhttp.AmzCopySource, "/"+bucket+"/"+source)
			rec := httptest.NewRecorder()
			router.ServeHTTP(rec, req)
			want := ErrUnsignedHeaders
			if strings.HasSuffix(mode, "v2") {
				want = ErrSignatureDoesNotMatch
			}
			var response APIErrorResponse
			if err := xml.Unmarshal(rec.Body.Bytes(), &response); err != nil {
				t.Errorf("expected S3 error XML, HTTP %d", rec.Code)
			} else if response.Code != getAPIError(want).Code || rec.Code != getAPIError(want).HTTPStatusCode {
				t.Errorf("got HTTP %d %s, want HTTP %d %s", rec.Code, response.Code, getAPIError(want).HTTPStatusCode, getAPIError(want).Code)
			}
			if _, err := obj.GetObjectInfo(ctx, bucket, destination, ObjectOptions{}); !isErrObjectNotFound(err) {
				t.Errorf("rejected request must leave destination absent, got %v", err)
			}
		})
	}

	for _, method := range []string{http.MethodPut, http.MethodGet} {
		name := "normal-presigned"
		body := strings.NewReader(data)
		if method == http.MethodGet {
			body = strings.NewReader("")
		}
		req, err := newTestRequest(method, getPutObjectURL("", bucket, name), int64(body.Len()), body)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Del(xhttp.AmzContentSha256)
		if err := preSignV4(req, cred.AccessKey, cred.SecretKey, 600); err != nil {
			t.Fatal(err)
		}
		// This payload-hash header is allowed outside SignedHeaders.
		req.Header.Set(xhttp.AmzContentSha256, unsignedPayload)
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("normal presigned %s: HTTP %d", method, rec.Code)
		}
		if method == http.MethodGet && rec.Body.String() != data {
			t.Fatal("presigned GET content differs")
		}
	}
	// Tagged reads must preserve the already-authenticated reader's permissions
	// when filtering retention metadata, even after synthesizing x-amz-tagging.
	for _, method := range []string{http.MethodGet, http.MethodHead} {
		req, err := newTestSignedRequestV4(method, getGetObjectURL("", bucket, source), 0, nil, cred.AccessKey, cred.SecretKey, nil)
		if err != nil {
			t.Fatal(err)
		}
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK || rec.Header().Get(xhttp.AmzObjectLockMode) != "GOVERNANCE" {
			t.Fatalf("tagged %s must preserve authorized retention metadata: HTTP %d, mode %q", method, rec.Code, rec.Header().Get(xhttp.AmzObjectLockMode))
		}
	}

	// These policy inputs come from XML/stored tags, rather than client headers.
	for _, method := range []string{http.MethodPut, http.MethodDelete} {
		body := ""
		expected := http.StatusNoContent
		if method == http.MethodPut {
			body = "<Tagging><TagSet><Tag><Key>test</Key><Value>updated</Value></Tag></TagSet></Tagging>"
			expected = http.StatusOK
		}
		req, err := newTestSignedRequestV4(method, getPutObjectURL("", bucket, source)+"?tagging",
			int64(len(body)), strings.NewReader(body), cred.AccessKey, cred.SecretKey, nil)
		if err != nil {
			t.Fatal(err)
		}
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		if rec.Code != expected {
			t.Fatalf("%s tagging: HTTP %d", method, rec.Code)
		}
		tags, err := obj.GetObjectTags(ctx, bucket, source, ObjectOptions{})
		if err != nil {
			t.Fatal(err)
		}
		want := ""
		if method == http.MethodPut {
			want = "test=updated"
		}
		if tags.String() != want {
			t.Fatalf("%s tagging did not persist expected tags", method)
		}
	}

	r, err := obj.GetObjectNInfo(ctx, bucket, source, nil, nil, ObjectOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	got, err := io.ReadAll(r)
	if err != nil || string(got) != data {
		t.Fatal("source content changed")
	}
}
