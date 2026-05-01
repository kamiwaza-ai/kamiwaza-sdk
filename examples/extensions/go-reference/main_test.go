// Tests for the HTTP layer: identity middleware (auth on/off, missing
// envelope → canonical 401) and the sample tool handler (POST happy path,
// 405 on wrong method, 400 on malformed body).
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

func TestHandleEcho_NonPost_Returns405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/tools/echo", nil)
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status: want 405, got %d", rr.Code)
	}
}

func TestHandleEcho_BadJSON_Returns400(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/tools/echo", strings.NewReader("not json"))
	rr := httptest.NewRecorder()
	handleEcho(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status: want 400, got %d", rr.Code)
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

func stringPtr(s string) *string { return &s }
