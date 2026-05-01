// Parity test: this Go extractor consumes the same canonical vectors as the
// Python (kamiwaza_extensions_lib.identity.extract_identity) and TS
// (@kamiwaza-ai/extensions-lib) runtime libs. If a vector behavior diverges
// between languages, the corresponding test fails in all three languages at
// the same case.
//
// Vectors live at kamiwaza-sdk/docs/extensions/non-sdk-flow/test-vectors.json
// (canonical, see ENG-3892 / D210 §4.2.11).
package identity

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

type vector struct {
	Case             string                 `json:"case"`
	Headers          map[string]string      `json:"headers"`
	ExpectedIdentity map[string]interface{} `json:"expected_identity,omitempty"`
	ShouldFailClass  string                 `json:"should_fail_class,omitempty"`
}

// vectorsPath walks up from this test file until it finds the repo root
// (identified by pyproject.toml — kamiwaza-sdk's top-level marker), then
// resolves the canonical test-vectors.json from there. Walking instead of
// counting `..` segments keeps the test resilient to subtree relocation.
func vectorsPath(t *testing.T) string {
	t.Helper()
	_, here, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed — cannot locate test file")
	}
	dir := filepath.Dir(here)
	for {
		if _, err := os.Stat(filepath.Join(dir, "pyproject.toml")); err == nil {
			return filepath.Join(dir, "docs", "extensions", "non-sdk-flow", "test-vectors.json")
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatalf("repo root marker pyproject.toml not found above %s", filepath.Dir(here))
		}
		dir = parent
	}
}

func loadVectors(t *testing.T) []vector {
	t.Helper()
	raw, err := os.ReadFile(vectorsPath(t))
	if err != nil {
		t.Fatalf("read vectors: %v", err)
	}
	var vecs []vector
	if err := json.Unmarshal(raw, &vecs); err != nil {
		t.Fatalf("parse vectors: %v", err)
	}
	if len(vecs) == 0 {
		t.Fatal("no vectors loaded")
	}
	return vecs
}

func headersFromMap(m map[string]string) http.Header {
	h := http.Header{}
	for k, v := range m {
		h.Set(k, v)
	}
	return h
}

func TestExtract_VectorParity(t *testing.T) {
	for _, v := range loadVectors(t) {
		t.Run(v.Case, func(t *testing.T) {
			id, err := Extract(headersFromMap(v.Headers))
			switch {
			case v.ExpectedIdentity != nil:
				if err != nil {
					t.Fatalf("unexpected error on happy-path vector: %v", err)
				}
				assertProjectedEqual(t, id, v.ExpectedIdentity)
			case v.ShouldFailClass == "misbound_auth":
				var mb *MisboundAuthError
				if !errors.As(err, &mb) {
					t.Fatalf("expected MisboundAuthError, got err=%v identity=%+v", err, id)
				}
			default:
				t.Fatalf("vector %q: unrecognized failure class %q", v.Case, v.ShouldFailClass)
			}
		})
	}
}

// assertProjectedEqual marshals the Identity to its JSON shape and compares
// every key the vector pins. A regression that drops a field entirely from
// the marshalled shape is caught — `_, present := got[key]` distinguishes
// "absent" from "explicit null".
func assertProjectedEqual(t *testing.T, id *Identity, expected map[string]interface{}) {
	t.Helper()
	enc, err := json.Marshal(id)
	if err != nil {
		t.Fatalf("marshal identity: %v", err)
	}
	var got map[string]interface{}
	if err := json.Unmarshal(enc, &got); err != nil {
		t.Fatalf("unmarshal identity: %v", err)
	}
	for key, want := range expected {
		actual, present := got[key]
		if !present {
			t.Errorf("field %q missing from marshalled Identity (regression: field dropped from struct)", key)
			continue
		}
		if !reflect.DeepEqual(actual, want) {
			t.Errorf("field %q: want %#v, got %#v", key, want, actual)
		}
	}
}

func TestExtract_WhitespaceOnlyUserID(t *testing.T) {
	h := headersFromMap(map[string]string{
		"X-User-Id":     "   ",
		"X-Workroom-Id": "w1",
	})
	_, err := Extract(h)
	var mb *MisboundAuthError
	if !errors.As(err, &mb) {
		t.Fatalf("whitespace-only X-User-Id should raise misbound_auth, got %v", err)
	}
}

func TestExtract_OptionalFieldsAbsent_ProduceNullJSON(t *testing.T) {
	h := headersFromMap(map[string]string{
		"X-User-Id":     "u1",
		"X-Workroom-Id": "w1",
	})
	id, err := Extract(h)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	enc, _ := json.Marshal(id)
	var got map[string]interface{}
	_ = json.Unmarshal(enc, &got)
	for _, key := range []string{"email", "name", "system_high", "workroom_role", "request_id"} {
		actual, present := got[key]
		if !present {
			t.Errorf("field %q dropped from marshalled Identity", key)
			continue
		}
		if actual != nil {
			t.Errorf("field %q: want JSON null, got %#v", key, actual)
		}
	}
}

func TestExtract_CaseInsensitiveHeaders(t *testing.T) {
	// http.Header.Set canonicalizes; pin that lowercase input still works.
	h := http.Header{}
	h.Set("x-user-id", "u1")
	h.Set("x-workroom-id", "w1")
	id, err := Extract(h)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id.UserID == nil || *id.UserID != "u1" {
		t.Fatalf("user_id: got %v", id.UserID)
	}
}

func TestParseRoles(t *testing.T) {
	cases := map[string][]string{
		"":                       {},
		",":                      {},
		",,,":                    {},
		"member":                 {"member"},
		"member,editor":          {"member", "editor"},
		" ,member, ":             {"member"},
		"member,":                {"member"},
		" member , , editor ":    {"member", "editor"},
		"member, editor, admin ": {"member", "editor", "admin"},
	}
	for input, want := range cases {
		got := parseRoles(input)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("parseRoles(%q): want %v, got %v", input, want, got)
		}
	}
}

func TestStrip(t *testing.T) {
	cases := map[string]*string{
		"":      nil,
		"   ":   nil,
		"\t\n":  nil,
		" foo ": ptr("foo"),
	}
	for input, want := range cases {
		got := strip(input)
		switch {
		case want == nil && got == nil:
			// ok
		case want == nil || got == nil:
			t.Errorf("strip(%q): want %v, got %v", input, want, got)
		case *got != *want:
			t.Errorf("strip(%q): want %q, got %q", input, *want, *got)
		}
	}
}

func ptr(s string) *string { return &s }
