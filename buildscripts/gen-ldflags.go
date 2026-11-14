//go:build ignore
// +build ignore

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

package main

import (
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Build metadata becomes part of the -ldflags string, so it must not contain
// separators or option text that would add further linker flags.
var (
	commitIDRe     = regexp.MustCompile(`^[0-9a-f]{40}$`)
	releaseFieldRe = regexp.MustCompile(`^([A-Za-z0-9][A-Za-z0-9._-]{0,63})?$`)
)

func mustMatch(re *regexp.Regexp, name, value string) {
	if !re.MatchString(value) {
		fmt.Fprintf(os.Stderr, "gen-ldflags: invalid %s %q: must match %s\n", name, value, re)
		os.Exit(1)
	}
}

func genLDFlags(version, commit string) string {
	mustMatch(commitIDRe, "commit ID", commit)
	releaseTag, date := releaseTag(version)
	copyrightYear := strconv.Itoa(date.Year())
	ldflagsStr := "-s -w"
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.Version=" + version
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.CopyrightYear=" + copyrightYear
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.ReleaseTag=" + releaseTag
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.CommitID=" + commit
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.ShortCommitID=" + commit[:12]
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.GOPATH=" + os.Getenv("GOPATH")
	ldflagsStr += " -X github.com/relvo-labs/granary/cmd.GOROOT=" + os.Getenv("GOROOT")
	return ldflagsStr
}

// genReleaseTag prints release tag to the console for easy git tagging.
func releaseTag(version string) (string, time.Time) {
	mustMatch(releaseFieldRe, "MINIO_RELEASE", os.Getenv("MINIO_RELEASE"))
	mustMatch(releaseFieldRe, "MINIO_HOTFIX", os.Getenv("MINIO_HOTFIX"))

	relPrefix := "DEVELOPMENT"
	if prefix := os.Getenv("MINIO_RELEASE"); prefix != "" {
		relPrefix = prefix
	}

	relSuffix := ""
	if hotfix := os.Getenv("MINIO_HOTFIX"); hotfix != "" {
		relSuffix = hotfix
	}

	relTag := strings.Replace(version, " ", "-", -1)
	relTag = strings.Replace(relTag, ":", "-", -1)
	t, err := time.Parse("2006-01-02T15-04-05Z", relTag)
	if err != nil {
		panic(err)
	}
	relTag = strings.Replace(relTag, ",", "", -1)
	relTag = relPrefix + "." + relTag
	if relSuffix != "" {
		relTag += "." + relSuffix
	}

	return relTag, t
}

// commitID returns the abbreviated commit-id hash of the last commit.
func commitID() string {
	// git log --format="%H" -n1
	var (
		commit []byte
		err    error
	)
	cmdName := "git"
	cmdArgs := []string{"log", "--format=%H", "-n1"}
	if commit, err = exec.Command(cmdName, cmdArgs...).Output(); err != nil {
		fmt.Fprintln(os.Stderr, "Error generating git commit-id: ", err)
		os.Exit(1)
	}

	return strings.TrimSpace(string(commit))
}

func commitTime() time.Time {
	// git log --format=%cD -n1
	var (
		commitUnix []byte
		err        error
	)
	cmdName := "git"
	cmdArgs := []string{"log", "--format=%cI", "-n1"}
	if commitUnix, err = exec.Command(cmdName, cmdArgs...).Output(); err != nil {
		fmt.Fprintln(os.Stderr, "Error generating git commit-time: ", err)
		os.Exit(1)
	}

	t, err := time.Parse(time.RFC3339, strings.TrimSpace(string(commitUnix)))
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error generating git commit-time: ", err)
		os.Exit(1)
	}

	return t.UTC()
}

// Usage: gen-ldflags.go [version [commit-id]]. Both default to the git
// metadata of the last commit; the Docker build passes them explicitly
// because .git is not part of its build context.
func main() {
	var version, commit string
	if len(os.Args) > 1 {
		version = os.Args[1]
	} else {
		version = commitTime().Format(time.RFC3339)
	}
	if len(os.Args) > 2 {
		commit = os.Args[2]
	} else {
		commit = commitID()
	}

	fmt.Println(genLDFlags(version, commit))
}
