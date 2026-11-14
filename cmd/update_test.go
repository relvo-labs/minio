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
	"net/http/httptest"
	"os"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestMinioVersionToReleaseTime(t *testing.T) {
	testCases := []struct {
		version    string
		isOfficial bool
	}{
		{"2017-09-29T19:16:56Z", true},
		{"RELEASE.2017-09-29T19-16-56Z", false},
		{"DEVELOPMENT.GOGET", false},
	}
	for i, testCase := range testCases {
		_, err := minioVersionToReleaseTime(testCase.version)
		if (err == nil) != testCase.isOfficial {
			t.Errorf("Test %d: Expected %v but got %v",
				i+1, testCase.isOfficial, err == nil)
		}
	}
}

// Tests user agent string.
func TestUserAgent(t *testing.T) {
	testCases := []struct {
		envName     string
		envValue    string
		mode        string
		expectedStr string
	}{
		{
			envName:     "",
			envValue:    "",
			mode:        globalMinioModeFS,
			expectedStr: fmt.Sprintf("MinIO (%s; %s; %s; source DEVELOPMENT.GOGET DEVELOPMENT.GOGET DEVELOPMENT.GOGET", runtime.GOOS, runtime.GOARCH, globalMinioModeFS),
		},
		{
			envName:     "MESOS_CONTAINER_NAME",
			envValue:    "mesos-11111",
			mode:        globalMinioModeErasure,
			expectedStr: fmt.Sprintf("MinIO (%s; %s; %s; %s; source DEVELOPMENT.GOGET DEVELOPMENT.GOGET DEVELOPMENT.GOGET universe-%s", runtime.GOOS, runtime.GOARCH, globalMinioModeErasure, "dcos", "mesos-1111"),
		},
		{
			envName:     "KUBERNETES_SERVICE_HOST",
			envValue:    "10.11.148.5",
			mode:        globalMinioModeErasure,
			expectedStr: fmt.Sprintf("MinIO (%s; %s; %s; %s; source DEVELOPMENT.GOGET DEVELOPMENT.GOGET DEVELOPMENT.GOGET", runtime.GOOS, runtime.GOARCH, globalMinioModeErasure, "kubernetes"),
		},
	}

	for i, testCase := range testCases {
		if testCase.envName != "" {
			t.Setenv(testCase.envName, testCase.envValue)
			if testCase.envName == "MESOS_CONTAINER_NAME" {
				t.Setenv("MARATHON_APP_LABEL_DCOS_PACKAGE_VERSION", "mesos-1111")
			}
		}

		str := getUserAgent(testCase.mode)
		expectedStr := testCase.expectedStr
		if IsDocker() {
			expectedStr = strings.ReplaceAll(expectedStr, "; source", "; docker; source")
		}
		if !strings.Contains(str, expectedStr) {
			t.Errorf("Test %d: expected: %s, got: %s", i+1, expectedStr, str)
		}
		os.Unsetenv("MARATHON_APP_LABEL_DCOS_PACKAGE_VERSION")
		os.Unsetenv(testCase.envName)
	}
}

// Tests if the environment we are running is in DCOS.
func TestIsDCOS(t *testing.T) {
	t.Setenv("MESOS_CONTAINER_NAME", "mesos-1111")
	dcos := IsDCOS()
	if !dcos {
		t.Fatalf("Expected %t, got %t", true, dcos)
	}
	os.Unsetenv("MESOS_CONTAINER_NAME")
	dcos = IsDCOS()
	if dcos {
		t.Fatalf("Expected %t, got %t", false, dcos)
	}
}

// Tests if the environment we are running is in kubernetes.
func TestIsKubernetes(t *testing.T) {
	t.Setenv("KUBERNETES_SERVICE_HOST", "10.11.148.5")
	kubernetes := IsKubernetes()
	if !kubernetes {
		t.Fatalf("Expected %t, got %t", true, kubernetes)
	}
	os.Unsetenv("KUBERNETES_SERVICE_HOST")

	kubernetes = IsKubernetes()
	if kubernetes {
		t.Fatalf("Expected %t, got %t", false, kubernetes)
	}
}

// Tests if the environment we are running is Helm chart.
func TestGetHelmVersion(t *testing.T) {
	createTempFile := func(content string) string {
		tmpfile, err := os.CreateTemp(t.TempDir(), "helm-testfile-")
		if err != nil {
			t.Fatalf("Unable to create temporary file. %s", err)
		}
		if _, err = tmpfile.WriteString(content); err != nil {
			t.Fatalf("Unable to create temporary file. %s", err)
		}
		if err = tmpfile.Close(); err != nil {
			t.Fatalf("Unable to create temporary file. %s", err)
		}
		return tmpfile.Name()
	}

	filename := createTempFile(
		`app="virtuous-rat-minio"
chart="minio-0.1.3"
heritage="Tiller"
pod-template-hash="818089471"`)

	defer os.Remove(filename)

	testCases := []struct {
		filename       string
		expectedResult string
	}{
		{"", ""},
		{"/tmp/non-existing-file", ""},
		{filename, "minio-0.1.3"},
	}

	for _, testCase := range testCases {
		result := getHelmVersion(testCase.filename)

		if testCase.expectedResult != result {
			t.Fatalf("result: expected: %v, got: %v", testCase.expectedResult, result)
		}
	}
}

// The peer self-update routes stay registered so that a peer running an older
// binary gets a clean error; they still authenticate and never read the body.
func TestPeerSelfUpdateHandlersDisabled(t *testing.T) {
	server := &peerRESTServer{}
	handlers := map[string]http.HandlerFunc{
		peerRESTMethodVerifyBinary: server.VerifyBinaryHandler,
		peerRESTMethodCommitBinary: server.CommitBinaryHandler,
	}
	for method, handler := range handlers {
		for _, authenticated := range []bool{false, true} {
			const payload = "binary"
			body := strings.NewReader(payload)
			req := httptest.NewRequest(http.MethodPost, peerRESTPath+method, body)
			if authenticated {
				req.Header.Set("Authorization", "Bearer "+globalNodeAuthToken)
				req.Header.Set("X-Minio-Time", strconv.FormatInt(time.Now().UnixNano(), 10))
			}
			rec := httptest.NewRecorder()
			handler(rec, req)

			if rec.Code != http.StatusForbidden {
				t.Errorf("%s authenticated=%v: status %d, want %d", method, authenticated, rec.Code, http.StatusForbidden)
			}
			gotDisabled := rec.Body.String() == errSelfUpdateDisabled.Error()
			if gotDisabled != authenticated {
				t.Errorf("%s authenticated=%v: unexpected response %q", method, authenticated, rec.Body.String())
			}
			if body.Len() != len(payload) {
				t.Errorf("%s authenticated=%v: request body was read", method, authenticated)
			}
		}
	}
}
