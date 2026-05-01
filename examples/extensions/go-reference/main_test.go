// Tests for the HTTP layer: identity middleware (auth on/off, missing
// envelope → canonical 401) and the sample tool handler (POST happy path,
// 405 on wrong method, 400/413 on malformed/oversize body).
package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/kamiwaza/kamiwaza-sdk/examples/extensions/go-reference/internal/identity"
)

func TestHandleHealth(t *testing.T) {
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	handleHealth(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d", rr.Code)
	}
	var body map[string]string
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body["status"] != "ok" {
		t.Errorf("status field: want %q, got %q", "ok", body["status"])
	}
}

func TestIdentityMiddleware_AuthOff_BypassesExtract(t *testing.T) {
	var observed *identity.Identity
	var observedOK bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observed, observedOK = r.Context().Value(identityCtxKey{}).(*identity.Identity)
		w.WriteHeader(http.StatusOK)
	})
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil) // no headers at all
	identityMiddleware(false, next).ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d", rr.Code)
	}
	if observedOK || observed != nil {
		t.Errorf("identity should be absent under auth-off, got %+v ok=%v", observed, observedOK)
	}
}

// Auth-off must not parse identity even when envelope headers are present
// — defends against a future "helpful" optimization that runs Extract on
// the auth-off path too.
func TestIdentityMiddleware_AuthOff_IgnoresEnvelopeHeaders(t *testing.T) {
	var observed *identity.Identity
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observed, _ = r.Context().Value(identityCtxKey{}).(*identity.Identity)
	})
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("X-User-Id", "u1")
	req.Header.Set("X-Workroom-Id", "w1")
	rr := httptest.NewRecorder()
	identityMiddleware(false, next).ServeHTTP(rr, req)
	if observed != nil {
		t.Errorf("auth-off must not populate identity even with envelope, got %+v", observed)
	}
}

func TestIdentityMiddleware_AuthOn_PopulatesContext(t *testing.T) {
	var observed *identity.Identity
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observed, _ = r.Context().Value(identityCtxKey{}).(*identity.Identity)
		w.WriteHeader(http.StatusOK)
	})
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("X-User-Id", "u1")
	req.Header.Set("X-Workroom-Id", "w1")
	rr := httptest.NewRecorder()
	identityMiddleware(true, next).ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d", rr.Code)
	}
	if observed == nil || observed.UserID == nil || *observed.UserID != "u1" {
		t.Fatalf("identity not populated: %+v", observed)
	}
}

// Wire-up integration: the identityMiddleware must populate the context
// key that handleEcho reads. Tested separately from the unit-level
// middleware test to catch a key-mismatch regression that would slip past
// either test alone.
func TestIdentityMiddleware_HandleEcho_WireUp(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader(`{"message":"hi"}`))
	req.Header.Set("X-User-Id", "u1")
	req.Header.Set("X-Workroom-Id", "w1")
	rr := httptest.NewRecorder()
	identityMiddleware(true, http.HandlerFunc(handleEcho)).ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d body=%s", rr.Code, rr.Body.String())
	}
	var body struct {
		Echo     string             `json:"echo"`
		Identity *identity.Identity `json:"identity"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.Identity == nil || body.Identity.UserID == nil || *body.Identity.UserID != "u1" {
		t.Errorf("identity not threaded middleware → handler: %+v", body.Identity)
	}
}

func TestIdentityMiddleware_AuthOn_MissingEnvelope_Returns401MisboundAuth(t *testing.T) {
	called := false
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	req := httptest.NewRequest(http.MethodPost, "/", nil) // no envelope headers
	rr := httptest.NewRecorder()
	identityMiddleware(true, next).ServeHTTP(rr, req)
	if called {
		t.Fatal("next handler must not be called when envelope is missing under auth-on")
	}
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("status: want 401, got %d", rr.Code)
	}
	// Pin the canonical JSON shape from non-sdk-flow.md §5.
	var body struct {
		Error struct {
			Class   string `json:"class"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.Error.Class != "misbound_auth" {
		t.Errorf("error.class: want %q, got %q", "misbound_auth", body.Error.Class)
	}
	if body.Error.Message == "" {
		t.Error("error.message should be non-empty")
	}
}

func TestHandleEcho_NonPost_Returns405WithAllowHeader(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/tools/echo", nil)
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status: want 405, got %d", rr.Code)
	}
	if got := rr.Header().Get("Allow"); got != http.MethodPost {
		t.Errorf("Allow header: want %q, got %q", http.MethodPost, got)
	}
	if class := decodeErrorClass(t, rr); class != "kamiwaza_runtime_error" {
		t.Errorf("error.class: want kamiwaza_runtime_error, got %q", class)
	}
}

func TestHandleEcho_BadJSON_Returns400(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader("not json"))
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status: want 400, got %d", rr.Code)
	}
	if class := decodeErrorClass(t, rr); class != "kamiwaza_runtime_error" {
		t.Errorf("error.class: want kamiwaza_runtime_error, got %q", class)
	}
}

// Strict decoder rejects unknown fields — exercised here so a future
// permissive-decode regression breaks the test.
func TestHandleEcho_UnknownField_Returns400(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader(`{"message":"hi","extra":"x"}`))
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status: want 400, got %d", rr.Code)
	}
}

// Trailing JSON after the first object is rejected — pins dec.More() check.
func TestHandleEcho_TrailingData_Returns400(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader(`{"message":"hi"} {"junk":1}`))
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status: want 400, got %d", rr.Code)
	}
}

// MaxBytesReader rejection: body of maxBodyBytes+1 must surface as 413.
// Locks the cap in so a future change to the literal is caught by tests.
func TestHandleEcho_OversizeBody_Returns413(t *testing.T) {
	big := strings.NewReader(`{"message":"` + strings.Repeat("a", int(maxBodyBytes)) + `"}`)
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", big)
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status: want 413, got %d body=%s", rr.Code, rr.Body.String())
	}
	if class := decodeErrorClass(t, rr); class != "kamiwaza_runtime_error" {
		t.Errorf("error.class: want kamiwaza_runtime_error, got %q", class)
	}
}

func TestHandleEcho_AuthOff_ReturnsNullIdentity(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader(`{"message":"hi"}`))
	rr := httptest.NewRecorder()
	handleEcho(rr, req) // no identity in context (auth-off path)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d", rr.Code)
	}
	var body map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body["echo"] != "hi" {
		t.Errorf("echo: want %q, got %v", "hi", body["echo"])
	}
	if v, ok := body["identity"]; !ok || v != nil {
		t.Errorf("identity: want explicit null, got present=%v value=%v", ok, v)
	}
}

func TestHandleEcho_AuthOn_ReturnsPopulatedIdentity(t *testing.T) {
	id := &identity.Identity{UserID: stringPtr("u1"), WorkroomID: stringPtr("w1")}
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader(`{"message":"hi"}`))
	req = req.WithContext(context.WithValue(req.Context(), identityCtxKey{}, id))
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status: want 200, got %d", rr.Code)
	}
	var body struct {
		Echo     string             `json:"echo"`
		Identity *identity.Identity `json:"identity"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.Identity == nil || body.Identity.UserID == nil || *body.Identity.UserID != "u1" {
		t.Errorf("identity: want u1, got %+v", body.Identity)
	}
}

// Pins the parity contract: parseUseAuth must agree with Python's truthy
// rules from kamiwaza_extensions_lib/config.py:57. A regression here is
// the kind of fail-open default that turns a "reference" into a footgun.
func TestParseUseAuth(t *testing.T) {
	cases := []struct {
		in   string
		want bool
	}{
		{"", true},         // unset/default → fail-secure
		{"   ", true},      // whitespace-only → default
		{"true", true},     // explicit on
		{"True", true},     // case-insensitive
		{"TRUE", true},     // case-insensitive
		{"yes", true},      // anything non-falsy
		{"1", true},        // anything non-falsy
		{"false", false},   // explicit off
		{"FALSE", false},   // case-insensitive off
		{"False", false},   // case-insensitive off
		{"0", false},       // explicit off
		{"no", false},      // explicit off
		{" false ", false}, // trimmed
	}
	for _, c := range cases {
		if got := parseUseAuth(c.in); got != c.want {
			t.Errorf("parseUseAuth(%q): want %v, got %v", c.in, c.want, got)
		}
	}
}

func decodeErrorClass(t *testing.T, rr *httptest.ResponseRecorder) string {
	t.Helper()
	var body struct {
		Error struct {
			Class string `json:"class"`
		} `json:"error"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode error body: %v", err)
	}
	return body.Error.Class
}

func stringPtr(s string) *string { return &s }
