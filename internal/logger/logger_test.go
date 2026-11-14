// Copyright (c) 2026 Granary contributors (unofficial fork of MinIO; not
// affiliated with or endorsed by MinIO, Inc.).
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
//
// SPDX-License-Identifier: AGPL-3.0-or-later

package logger

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type modulePathProbe struct{}

func TestTrimTraceModulePath(t *testing.T) {
	// Derive the module path from the compiler so the test follows go.mod
	// instead of repeating the literal used by trimStrings.
	module := strings.TrimSuffix(reflect.TypeOf(modulePathProbe{}).PkgPath(), "/internal/logger")
	if module == reflect.TypeOf(modulePathProbe{}).PkgPath() {
		t.Fatalf("unexpected package path %q", module)
	}

	previous := trimStrings
	t.Cleanup(func() { trimStrings = previous })
	Init("", "")
	for _, rel := range []string{"cmd/server-main.go", "internal/logger/logger.go"} {
		path := module + "/" + rel
		if got := filepath.ToSlash(trimTrace(path)); got != rel {
			t.Errorf("trimTrace(%q) = %q, want %q", path, got, rel)
		}
	}
}
