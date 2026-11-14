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
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"runtime"
	"strings"
	"sync/atomic"
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

func TestReleaseTagToNFromTimeConversion(t *testing.T) {
	utcLoc, _ := time.LoadLocation("")
	testCases := []struct {
		t      time.Time
		tag    string
		errStr string
	}{
		{
			time.Date(2017, time.September, 29, 19, 16, 56, 0, utcLoc),
			"RELEASE.2017-09-29T19-16-56Z", "",
		},
		{
			time.Date(2017, time.August, 5, 0, 0, 53, 0, utcLoc),
			"RELEASE.2017-08-05T00-00-53Z", "",
		},
		{
			time.Now().UTC(), "2017-09-29T19:16:56Z",
			"2017-09-29T19:16:56Z is not a valid release tag",
		},
		{
			time.Now().UTC(), "DEVELOPMENT.GOGET",
			"DEVELOPMENT.GOGET is not a valid release tag",
		},
		{
			time.Date(2017, time.August, 5, 0, 0, 53, 0, utcLoc),
			"RELEASE.2017-08-05T00-00-53Z.hotfix", "",
		},
		{
			time.Date(2017, time.August, 5, 0, 0, 53, 0, utcLoc),
			"RELEASE.2017-08-05T00-00-53Z.hotfix.aaaa", "",
		},
	}
	for i, testCase := range testCases {
		if testCase.errStr != "" {
			got := releaseTimeToReleaseTag(testCase.t)
			if got != testCase.tag && testCase.errStr == "" {
				t.Errorf("Test %d: Expected %v but got %v", i+1, testCase.tag, got)
			}
		}
		tagTime, err := releaseTagToReleaseTime(testCase.tag)
		if err != nil && err.Error() != testCase.errStr {
			t.Errorf("Test %d: Expected %v but got %v", i+1, testCase.errStr, err.Error())
		}
		if err == nil && !tagTime.Equal(testCase.t) {
			t.Errorf("Test %d: Expected %v but got %v", i+1, testCase.t, tagTime)
		}
	}
}

func TestDownloadURL(t *testing.T) {
	for _, name := range []string{"KUBERNETES_SERVICE_HOST", "MESOS_CONTAINER_NAME"} {
		t.Setenv(name, "test")
	}
	if got := getDownloadURL(releaseTimeToReleaseTag(UTCNow())); got != "" {
		t.Fatalf("disabled update channel advertised %q", got)
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

func TestDownloadReleaseData(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		t.Error("self-update made an HTTP request")
	}))
	defer server.Close()
	u, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "server"} {
		if content, err := downloadReleaseURL(u, time.Second, mode); content != "" || err != errSelfUpdateDisabled {
			t.Fatalf("downloadReleaseURL = %q, %v", content, err)
		}
		if _, _, err := getLatestReleaseTime(u, time.Second, mode); err != errSelfUpdateDisabled {
			t.Fatalf("getLatestReleaseTime = %v", err)
		}
		if _, err := getUpdateReaderFromURL(u, http.DefaultTransport, mode); err != errSelfUpdateDisabled {
			t.Fatalf("getUpdateReaderFromURL = %v", err)
		}
		if _, _, err := downloadBinary(u, mode); err != errSelfUpdateDisabled {
			t.Fatalf("downloadBinary = %v", err)
		}
		if err := verifyBinary(u, nil, "", mode, nil); err != errSelfUpdateDisabled {
			t.Fatalf("verifyBinary = %v", err)
		}
		checkUpdate(mode)
	}
	if err := commitBinary(); err != errSelfUpdateDisabled {
		t.Fatalf("commitBinary = %v", err)
	}
	if requests.Load() != 0 {
		t.Fatal("self-update contacted an update server")
	}
}

func TestParseReleaseData(t *testing.T) {
	releaseTime, _ := releaseTagToReleaseTime("RELEASE.2016-10-07T01-16-39Z")
	testCases := []struct {
		data                string
		expectedResult      time.Time
		expectedSha256hex   string
		expectedReleaseInfo string
		expectedErr         bool
	}{
		{"more than two fields", time.Time{}, "", "", true},
		{"more than", time.Time{}, "", "", true},
		{"more than.two.fields", time.Time{}, "", "", true},
		{"more minio.RELEASE.fields", time.Time{}, "", "", true},
		{"more minio.RELEASE.2016-10-07T01-16-39Z", time.Time{}, "", "", true},
		{
			"fbe246edbd382902db9a4035df7dce8cb441357d minio.RELEASE.2016-10-07T01-16-39Z\n", releaseTime, "fbe246edbd382902db9a4035df7dce8cb441357d",
			"minio.RELEASE.2016-10-07T01-16-39Z", false,
		},
		{
			"fbe246edbd382902db9a4035df7dce8cb441357d minio.RELEASE.2016-10-07T01-16-39Z.customer-hotfix\n", releaseTime, "fbe246edbd382902db9a4035df7dce8cb441357d",
			"minio.RELEASE.2016-10-07T01-16-39Z.customer-hotfix", false,
		},
	}

	for i, testCase := range testCases {
		sha256Sum, result, releaseInfo, err := parseReleaseData(testCase.data)
		if !testCase.expectedErr {
			if err != nil {
				t.Errorf("error case %d: expected no error, got: %v", i+1, err)
			}
		} else if err == nil {
			t.Errorf("error case %d: expected error got: %v", i+1, err)
		}
		if err == nil {
			if hex.EncodeToString(sha256Sum) != testCase.expectedSha256hex {
				t.Errorf("case %d: result: expected: %v, got: %x", i+1, testCase.expectedSha256hex, sha256Sum)
			}
			if !testCase.expectedResult.Equal(result) {
				t.Errorf("case %d: result: expected: %v, got: %v", i+1, testCase.expectedResult, result)
			}
			if testCase.expectedReleaseInfo != releaseInfo {
				t.Errorf("case %d: result: expected: %v, got: %v", i+1, testCase.expectedReleaseInfo, releaseInfo)
			}
		}
	}
}
