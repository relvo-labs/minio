// Copyright (c) 2015-2023 MinIO, Inc.
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
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/minio/madmin-go/v3"
	miniogo "github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"github.com/minio/pkg/v3/policy"
	"github.com/relvo-labs/granary/internal/auth"
	"github.com/relvo-labs/granary/internal/bucket/replication"
	xhttp "github.com/relvo-labs/granary/internal/http"
)

func newTaggingTestUser(t *testing.T) auth.Credentials {
	t.Helper()
	cred, err := auth.GetNewCredentials()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = globalIAMSys.CreateUser(t.Context(), cred.AccessKey, madmin.AddOrUpdateUserReq{
		SecretKey: cred.SecretKey, Status: madmin.AccountEnabled,
	}); err != nil {
		t.Fatal(err)
	}
	return cred
}

// Signed regular users use IAM policies; anonymous requests use bucket policies.
// Install the same bucket-scoped conditions in both to exercise both paths.
func setTaggingTestPolicy(t *testing.T, bucket string, cred auth.Credentials, statements string) {
	t.Helper()
	config := `{"Version":"2012-10-17","Statement":[` + statements + `]}`
	p, err := policy.ParseConfig(strings.NewReader(config))
	if err != nil {
		t.Fatal(err)
	}
	const name = "tagging-test-policy"
	if _, err = globalIAMSys.SetPolicy(t.Context(), name, *p); err != nil {
		t.Fatal(err)
	}
	if _, err = globalIAMSys.PolicyDBSet(t.Context(), cred.AccessKey, name, regUser, false); err != nil {
		t.Fatal(err)
	}
	bp, err := policy.ParseBucketPolicyConfig(strings.NewReader(strings.ReplaceAll(config, `"Effect":`, `"Principal":"*","Effect":`)), bucket)
	if err != nil {
		t.Fatal(err)
	}
	meta, _, err := globalBucketMetadataSys.GetConfig(t.Context(), bucket)
	if err != nil {
		t.Fatal(err)
	}
	meta.policyConfig = bp
	globalBucketMetadataSys.Set(bucket, meta)
}

func taggingTestRequest(t *testing.T, router http.Handler, method, target, body string, cred auth.Credentials) *httptest.ResponseRecorder {
	t.Helper()
	req, err := newTestRequest(method, target, int64(len(body)), strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if cred.AccessKey != "" {
		if err = signRequestV4(req, cred.AccessKey, cred.SecretKey); err != nil {
			t.Fatal(err)
		}
	}
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	return rec
}

func TestAPIDeleteObjectTaggingProxyAuthentication(t *testing.T) {
	ExecObjectLayerAPITest(ExecObjectLayerAPITestArgs{t: t, objAPITest: testAPIDeleteObjectTaggingProxyAuthentication})
}

func testAPIDeleteObjectTaggingProxyAuthentication(obj ObjectLayer, instanceType, bucket string, router http.Handler, root auth.Credentials, t *testing.T) {
	var removes, reads atomic.Int32
	var deniedTag atomic.Bool
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/remote-bucket/absent" || !r.URL.Query().Has("tagging") {
			t.Errorf("unexpected target request: %s %s", r.Method, r.URL.RequestURI())
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		switch r.Method {
		case http.MethodDelete:
			removes.Add(1)
			w.WriteHeader(http.StatusNoContent)
		case http.MethodGet:
			reads.Add(1)
			w.Header().Set(xhttp.ContentType, "application/xml")
			value := "value"
			if deniedTag.Load() {
				value = "denied"
			}
			fmt.Fprintf(w, `<Tagging><TagSet><Tag><Key>test</Key><Value>%s</Value></Tag></TagSet></Tagging>`, value)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	defer remote.Close()
	client, err := miniogo.New(strings.TrimPrefix(remote.URL, "http://"), &miniogo.Options{
		Creds: credentials.NewStaticV4(root.AccessKey, root.SecretKey, ""), Region: "us-east-1",
	})
	if err != nil {
		t.Fatal(err)
	}
	const arn = "arn:minio:replication::test:remote-bucket"
	meta := newBucketMetadata(bucket)
	meta.replicationConfig, err = replication.ParseConfig(strings.NewReader(`<ReplicationConfiguration><Rule><Status>Enabled</Status><Filter><Prefix></Prefix></Filter><Destination><Bucket>` + arn + `</Bucket></Destination></Rule></ReplicationConfiguration>`))
	if err != nil {
		t.Fatal(err)
	}
	globalBucketMetadataSys.Set(bucket, meta)
	previous := globalBucketTargetSys
	globalBucketTargetSys = &BucketTargetSys{
		arnRemotesMap: map[string]arnTarget{arn: {Client: &TargetClient{Client: client, Bucket: "remote-bucket"}}},
		targetsMap:    map[string][]madmin.BucketTarget{bucket: {{Arn: arn, TargetBucket: "remote-bucket", Credentials: &madmin.Credentials{}}}},
		hc:            map[string]epHealth{client.EndpointURL().Host: {Online: true}},
	}
	defer func() { globalBucketTargetSys = previous }()
	user := newTaggingTestUser(t)
	setTaggingTestPolicy(t, bucket, user, fmt.Sprintf(`{"Effect":"Allow","Action":"s3:DeleteObjectTagging","Resource":"arn:aws:s3:::%s/*","Condition":{"StringEquals":{"s3:ExistingObjectTag/test":"value","aws:username":%q}}}`, bucket, user.AccessKey))

	// Cover both a wholly absent object and a version absent from a local object.
	for _, version := range []string{"", "a3348c34-c352-4498-82f0-1098e8b34df9"} {
		if version != "" {
			if _, err := obj.PutObject(t.Context(), bucket, "absent", mustGetPutObjReader(t, strings.NewReader("data"), 4, "", ""), ObjectOptions{}); err != nil {
				t.Fatal(err)
			}
		}
		for _, mode := range []string{"anonymous", "forged-signature", "owner", "non-owner", "denied-non-owner", "denied-tag-condition"} {
			t.Run(instanceType+"/"+version+"/"+mode, func(t *testing.T) {
				deniedTag.Store(mode == "denied-tag-condition")
				target := getGetObjectURL("", bucket, "absent") + "?tagging"
				if version != "" {
					target += "&versionId=" + version
				}
				cred := root
				want := http.StatusNoContent
				switch mode {
				case "anonymous":
					cred = auth.AnonymousCredentials
					// A bucket policy conditioned on a signed principal cannot grant anonymous access.
					want = http.StatusForbidden
				case "forged-signature":
					cred.SecretKey += "invalid"
					want = http.StatusForbidden
				case "non-owner":
					cred = user
				case "denied-tag-condition":
					cred = user
					want = http.StatusForbidden
				case "denied-non-owner":
					cred = newTaggingTestUser(t)
					want = http.StatusForbidden
				}
				before, readsBefore := removes.Load(), reads.Load()
				rec := taggingTestRequest(t, router, http.MethodDelete, target, "", cred)
				if rec.Code != want {
					t.Errorf("HTTP %d, want %d", rec.Code, want)
				}
				if want == http.StatusForbidden {
					var response APIErrorResponse
					code := ErrAccessDenied
					if mode == "forged-signature" {
						code = ErrSignatureDoesNotMatch
					}
					if err := xml.Unmarshal(rec.Body.Bytes(), &response); err != nil || response.Code != getAPIError(code).Code {
						t.Errorf("expected S3 error %s", getAPIError(code).Code)
					}
				}
				expectedRemoves := int32(0)
				if want == http.StatusNoContent {
					expectedRemoves = 1
					if rec.Header().Get(xhttp.MinIOTaggingProxied) != "true" {
						t.Error("missing proxied response header")
					}
				}
				if got := removes.Load() - before; got != expectedRemoves {
					t.Errorf("replication target received %d RemoveObjectTagging calls, want %d", got, expectedRemoves)
				}
				if mode == "forged-signature" && reads.Load() != readsBefore {
					t.Error("forged request looked up remote tags before authentication")
				}
			})
		}
	}
}

func TestAPITaggingNonOwnerPolicy(t *testing.T) {
	ExecObjectLayerAPITest(ExecObjectLayerAPITestArgs{t: t, objAPITest: testAPITaggingNonOwnerPolicy})
}

func testAPITaggingNonOwnerPolicy(obj ObjectLayer, instanceType, bucket string, router http.Handler, _ auth.Credentials, t *testing.T) {
	globalBucketMetadataSys.Set(bucket, newBucketMetadata(bucket))
	user := newTaggingTestUser(t)
	for _, retention := range []bool{false, true} {
		statements := fmt.Sprintf(`{"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectTagging","s3:DeleteObjectTagging"],"Resource":"arn:aws:s3:::%s/*","Condition":{"StringEquals":{"s3:ExistingObjectTag/test":"value"}}},
		{"Effect":"Allow","Action":"s3:PutObjectTagging","Resource":"arn:aws:s3:::%s/*","Condition":{"StringEquals":{"s3:RequestObjectTag/test":"value"}}}`, bucket, bucket)
		if retention {
			statements += fmt.Sprintf(`,{"Effect":"Allow","Action":"s3:GetObjectRetention","Resource":"arn:aws:s3:::%s/*"}`, bucket)
		}
		setTaggingTestPolicy(t, bucket, user, statements)
		for _, storedTag := range []string{"value", "denied"} {
			for _, method := range []string{http.MethodGet, http.MethodHead, "GET-tagging", "PUT-tagging", "DELETE-tagging"} {
				for _, identity := range []struct {
					name string
					cred auth.Credentials
				}{{"non-owner", user}, {"anonymous", auth.AnonymousCredentials}} {
					t.Run(fmt.Sprintf("%s/%s/retention=%t/tag=%s/%s", instanceType, identity.name, retention, storedTag, method), func(t *testing.T) {
						const object = "tagged-policy-object"
						initialTag := storedTag
						if method == "PUT-tagging" {
							initialTag = "initial"
						}
						until := UTCNow().Add(time.Hour).Format(time.RFC3339)
						if _, err := obj.PutObject(t.Context(), bucket, object, mustGetPutObjReader(t, strings.NewReader("data"), 4, "", ""), ObjectOptions{UserDefined: map[string]string{
							xhttp.AmzObjectTagging:                              "test=" + initialTag,
							strings.ToLower(xhttp.AmzObjectLockMode):            "GOVERNANCE",
							strings.ToLower(xhttp.AmzObjectLockRetainUntilDate): until,
							strings.ToLower(xhttp.AmzObjectLockLegalHold):       "ON",
						}}); err != nil {
							t.Fatal(err)
						}
						target := getGetObjectURL("", bucket, object)
						verb, body := method, ""
						want := http.StatusOK
						if strings.HasSuffix(method, "-tagging") {
							verb = strings.TrimSuffix(method, "-tagging")
							target += "?tagging"
							switch verb {
							case http.MethodPut:
								body = `<Tagging><TagSet><Tag><Key>test</Key><Value>` + storedTag + `</Value></Tag></TagSet></Tagging>`
							case http.MethodDelete:
								want = http.StatusNoContent
							}
						}
						if storedTag == "denied" {
							want = http.StatusForbidden
						}
						rec := taggingTestRequest(t, router, verb, target, body, identity.cred)
						if rec.Code != want {
							t.Fatalf("HTTP %d, want %d", rec.Code, want)
						}
						if want == http.StatusOK && (method == http.MethodGet || method == http.MethodHead) {
							mode, date := "", ""
							if retention {
								mode, date = "GOVERNANCE", until
							}
							if rec.Header().Get(xhttp.AmzObjectLockMode) != mode || rec.Header().Get(xhttp.AmzObjectLockRetainUntilDate) != date {
								t.Error("retention metadata did not follow non-owner permissions")
							}
							if rec.Header().Get(xhttp.AmzObjectLockLegalHold) != "" {
								t.Error("legal hold metadata leaked without permission")
							}
						}
						if strings.HasSuffix(method, "-tagging") {
							tags, err := obj.GetObjectTags(t.Context(), bucket, object, ObjectOptions{})
							if err != nil {
								t.Fatal(err)
							}
							expected := "test=" + initialTag
							if method == "PUT-tagging" && want == http.StatusOK {
								expected = "test=" + storedTag
							}
							if method == "DELETE-tagging" && want == http.StatusNoContent {
								expected = ""
							}
							if tags.String() != expected {
								t.Error("tagging request left unexpected stored tags")
							}
						}
					})
				}
			}
		}
	}
}
