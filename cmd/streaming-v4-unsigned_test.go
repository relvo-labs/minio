// Copyright (c) 2026 Granary contributors
//
// This file is part of Granary, an unofficial fork derived from MinIO.
// SPDX-License-Identifier: AGPL-3.0-or-later

package cmd

import (
	"bufio"
	"io"
	"net/http"
	"strings"
	"testing"
)

type unsignedChunkedReadCounter struct {
	io.Reader
	reads int
}

func (r *unsignedChunkedReadCounter) Read(p []byte) (int, error) {
	r.reads++
	return r.Reader.Read(p)
}

func TestUnsignedChunkedReaderTerminal(t *testing.T) {
	for _, tt := range []struct {
		name, body string
		trailer    bool
		want       error
	}{
		{name: "EOF", body: "3\r\nabc\r\n0\r\n\r\n", want: io.EOF},
		{name: "trailer EOF", body: "3\r\nabc\r\n0\r\nx-amz-checksum-crc32:NSRBwg==\r\n\r\n", trailer: true, want: io.EOF},
		{name: "truncated chunk", body: "3\r\nab", want: io.ErrUnexpectedEOF},
		{name: "malformed chunk", body: "z\r\n", want: errMalformedEncoding},
		{name: "oversized chunk", body: "1000001\r\n", want: errChunkTooBig},
		{name: "truncated trailer", body: "0\r\nx-amz-checksum-crc32:", trailer: true, want: io.ErrUnexpectedEOF},
	} {
		t.Run(tt.name, func(t *testing.T) {
			body := &unsignedChunkedReadCounter{Reader: strings.NewReader(tt.body)}
			cr := &s3UnsignedChunkedReader{reader: bufio.NewReader(body)}
			if tt.trailer {
				cr.trailers = http.Header{"X-Amz-Checksum-Crc32": {""}}
			}
			var buf [16]byte
			var data strings.Builder
			for {
				n, err := cr.Read(buf[:])
				data.Write(buf[:n])
				if err == nil {
					continue
				}
				if err != tt.want {
					t.Fatalf("terminal error = %v, want %v", err, tt.want)
				}
				break
			}
			if tt.want == io.EOF {
				if data.String() != "abc" {
					t.Errorf("decoded payload = %q, want abc", data.String())
				}
				if tt.trailer && cr.trailers.Get("X-Amz-Checksum-Crc32") != "NSRBwg==" {
					t.Errorf("trailer was not populated: %v", cr.trailers)
				}
			}
			reads := body.reads
			for range 3 {
				if n, err := cr.Read(buf[:]); n != 0 || err != tt.want {
					t.Errorf("Read after terminal error = %d, %v, want 0, %v", n, err, tt.want)
				}
				if body.reads != reads {
					t.Errorf("Read touched underlying body: reads = %d, want %d", body.reads, reads)
				}
			}
		})
	}
}
