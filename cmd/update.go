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
	"bufio"
	"fmt"
	"net/http"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/minio/pkg/v3/env"
	"github.com/relvo-labs/granary/internal/logger"
	gopsutilcpu "github.com/shirou/gopsutil/v3/cpu"
)

// No automatic update channel is configured for this unofficial fork.
var errSelfUpdateDisabled = AdminError{
	Code:       AdminUpdateApplyFailure,
	Message:    "self-update is disabled in Granary; install a Granary release manually",
	StatusCode: http.StatusMethodNotAllowed,
}

// minioVersionToReleaseTime - parses a standard official release
// MinIO version string.
//
// An official binary's version string is the release time formatted
// with RFC3339 (in UTC) - e.g. `2017-09-29T19:16:56Z`
func minioVersionToReleaseTime(version string) (releaseTime time.Time, err error) {
	return time.Parse(time.RFC3339, version)
}

// IsDocker - returns if the environment minio is running in docker or
// not. The check is a simple file existence check.
//
// https://github.com/moby/moby/blob/master/daemon/initlayer/setup_unix.go
// https://github.com/containers/podman/blob/master/libpod/runtime.go
//
//	"/.dockerenv":        "file",
//	"/run/.containerenv": "file",
func IsDocker() bool {
	var err error
	for _, envfile := range []string{
		"/.dockerenv",
		"/run/.containerenv",
	} {
		_, err = os.Stat(envfile)
		if err == nil {
			return true
		}
	}
	if osIsNotExist(err) {
		// if none of the files are present we may be running inside
		// CRI-O, Containerd etc..
		// Fallback to our container specific ENVs if they are set.
		return env.IsSet("MINIO_ACCESS_KEY_FILE")
	}

	// Log error, as we will not propagate it to caller
	internalLogIf(GlobalContext, err)

	return err == nil
}

// IsDCOS returns true if minio is running in DCOS.
func IsDCOS() bool {
	// http://mesos.apache.org/documentation/latest/docker-containerizer/
	// Mesos docker containerizer sets this value
	return env.Get("MESOS_CONTAINER_NAME", "") != ""
}

// IsKubernetes returns true if minio is running in kubernetes.
func IsKubernetes() bool {
	// Kubernetes env used to validate if we are
	// indeed running inside a kubernetes pod
	// is KUBERNETES_SERVICE_HOST
	// https://github.com/kubernetes/kubernetes/blob/master/pkg/kubelet/kubelet_pods.go#L541
	return env.Get("KUBERNETES_SERVICE_HOST", "") != ""
}

// IsBOSH returns true if minio is deployed from a bosh package
func IsBOSH() bool {
	// "/var/vcap/bosh" exists in BOSH deployed instance.
	_, err := os.Stat("/var/vcap/bosh")
	if osIsNotExist(err) {
		return false
	}

	// Log error, as we will not propagate it to caller
	internalLogIf(GlobalContext, err)

	return err == nil
}

// MinIO Helm chart uses DownwardAPIFile to write pod label info to /podinfo/labels
// More info: https://kubernetes.io/docs/tasks/inject-data-application/downward-api-volume-expose-pod-information/#store-pod-fields
// Check if this is Helm package installation and report helm chart version
func getHelmVersion(helmInfoFilePath string) string {
	// Read the file exists.
	helmInfoFile, err := Open(helmInfoFilePath)
	if err != nil {
		// Log errors and return "" as MinIO can be deployed
		// without Helm charts as well.
		if !osIsNotExist(err) {
			reqInfo := (&logger.ReqInfo{}).AppendTags("helmInfoFilePath", helmInfoFilePath)
			ctx := logger.SetReqInfo(GlobalContext, reqInfo)
			internalLogIf(ctx, err)
		}
		return ""
	}
	defer helmInfoFile.Close()
	scanner := bufio.NewScanner(helmInfoFile)
	for scanner.Scan() {
		if strings.Contains(scanner.Text(), "chart=") {
			helmChartVersion := strings.TrimPrefix(scanner.Text(), "chart=")
			// remove quotes from the chart version
			return strings.Trim(helmChartVersion, `"`)
		}
	}

	return ""
}

// IsSourceBuild - returns if this binary is a non-official build from
// source code.
func IsSourceBuild() bool {
	_, err := minioVersionToReleaseTime(Version)
	return err != nil
}

// IsPCFTile returns if server is running in PCF
func IsPCFTile() bool {
	return env.Get("MINIO_PCF_TILE_VERSION", "") != ""
}

// DO NOT CHANGE USER AGENT STYLE.
// The style should be
//
//	MinIO (<OS>; <ARCH>[; <MODE>][; dcos][; kubernetes][; docker][; source]) MinIO/<VERSION> MinIO/<RELEASE-TAG> MinIO/<COMMIT-ID> [MinIO/universe-<PACKAGE-NAME>] [MinIO/helm-<HELM-VERSION>]
//
// Any change here should be discussed by opening an issue at
// https://github.com/minio/minio/issues.
func getUserAgent(mode string) string {
	userAgentParts := []string{}
	// Helper function to concisely append a pair of strings to a
	// the user-agent slice.
	uaAppend := func(p, q string) {
		userAgentParts = append(userAgentParts, p, q)
	}
	uaAppend(MinioUAName, " (")
	uaAppend("", runtime.GOOS)
	uaAppend("; ", runtime.GOARCH)
	if mode != "" {
		uaAppend("; ", mode)
	}
	if IsDCOS() {
		uaAppend("; ", "dcos")
	}
	if IsKubernetes() {
		uaAppend("; ", "kubernetes")
	}
	if IsDocker() {
		uaAppend("; ", "docker")
	}
	if IsBOSH() {
		uaAppend("; ", "bosh")
	}
	if IsSourceBuild() {
		uaAppend("; ", "source")
	}

	uaAppend(" ", Version)
	uaAppend(" ", ReleaseTag)
	uaAppend(" ", CommitID)
	if IsDCOS() {
		universePkgVersion := env.Get("MARATHON_APP_LABEL_DCOS_PACKAGE_VERSION", "")
		// On DC/OS environment try to the get universe package version.
		if universePkgVersion != "" {
			uaAppend(" universe-", universePkgVersion)
		}
	}

	if IsKubernetes() {
		// In Kubernetes environment, try to fetch the helm package version
		helmChartVersion := getHelmVersion("/podinfo/labels")
		if helmChartVersion != "" {
			uaAppend(" helm-", helmChartVersion)
		}
		// In Kubernetes environment, try to fetch the Operator, VSPHERE plugin version
		opVersion := env.Get("MINIO_OPERATOR_VERSION", "")
		if opVersion != "" {
			uaAppend(" operator-", opVersion)
		}
		vsphereVersion := env.Get("MINIO_VSPHERE_PLUGIN_VERSION", "")
		if vsphereVersion != "" {
			uaAppend(" vsphere-plugin-", vsphereVersion)
		}
	}

	if IsPCFTile() {
		pcfTileVersion := env.Get("MINIO_PCF_TILE_VERSION", "")
		if pcfTileVersion != "" {
			uaAppend(" pcf-tile-", pcfTileVersion)
		}
	}
	uaAppend("; ", "")

	if cpus, err := gopsutilcpu.Info(); err == nil && len(cpus) > 0 {
		cpuMap := make(map[string]struct{}, len(cpus))
		coreMap := make(map[string]struct{}, len(cpus))
		for i := range cpus {
			cpuMap[cpus[i].PhysicalID] = struct{}{}
			coreMap[cpus[i].CoreID] = struct{}{}
		}
		cpu := cpus[0]
		uaAppend(" CPU ", fmt.Sprintf("(total_cpus:%d, total_cores:%d; vendor:%s; family:%s; model:%s; stepping:%d; model_name:%s)",
			len(cpuMap), len(coreMap), cpu.VendorID, cpu.Family, cpu.Model, cpu.Stepping, cpu.ModelName))
	}
	uaAppend(")", "")

	return strings.Join(userAgentParts, "")
}
