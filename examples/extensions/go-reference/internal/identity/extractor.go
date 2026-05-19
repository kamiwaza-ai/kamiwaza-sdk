// Package identity parses the Kamiwaza envelope headers stamped by Traefik's
// ForwardAuth middleware into an Identity value. Header parsing only — no
// HMAC, no shared secret, no canonicalization, no TTL check. The trust
// boundary is Traefik (kamiwaza-sdk/docs/extensions/non-sdk-flow.md §8).
package identity

import (
	"net/http"
	"strings"
)

// Identity mirrors the fields pinned by docs/extensions/non-sdk-flow/test-vectors.json.
// X-Auth-Token is deliberately not stored: the bearer credential lives in
// request scope (read it from r.Header directly when forwarding to the
// platform) so identity payloads never leak it through logs or error bodies.
type Identity struct {
	UserID       *string  `json:"user_id"`
	Email        *string  `json:"email"`
	Name         *string  `json:"name"`
	Roles        []string `json:"roles"`
	SystemHigh   *string  `json:"system_high"`
	WorkroomID   *string  `json:"workroom_id"`
	WorkroomRole *string  `json:"workroom_role"`
	RequestID    *string  `json:"request_id"`
}

// MisboundAuthError is the canonical "request did not come through Traefik,
// or platform did not populate the envelope" failure (non-sdk-flow.md §5).
// Maps to HTTP 401 with class "misbound_auth". The message is read-only
// from outside the package via Error().
type MisboundAuthError struct{ msg string }

func (e *MisboundAuthError) Error() string { return e.msg }

// Extract reads envelope headers into an Identity. Returns *MisboundAuthError
// when X-User-Id or X-Workroom-Id is missing or whitespace-only.
func Extract(h http.Header) (*Identity, error) {
	userID := strip(h.Get("X-User-Id"))
	workroomID := strip(h.Get("X-Workroom-Id"))
	if userID == nil {
		return nil, &MisboundAuthError{msg: "Required envelope header X-User-Id missing or empty"}
	}
	if workroomID == nil {
		return nil, &MisboundAuthError{msg: "Required envelope header X-Workroom-Id missing or empty"}
	}
	return &Identity{
		UserID:       userID,
		Email:        strip(h.Get("X-User-Email")),
		Name:         strip(h.Get("X-User-Name")),
		Roles:        parseRoles(h.Get("X-User-Roles")),
		SystemHigh:   strip(h.Get("X-User-System-High")),
		WorkroomID:   workroomID,
		WorkroomRole: strip(h.Get("X-User-Workroom-Role")),
		RequestID:    strip(h.Get("X-Request-Id")),
	}, nil
}

func strip(v string) *string {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	return &v
}

func parseRoles(raw string) []string {
	out := []string{}
	for _, p := range strings.Split(raw, ",") {
		if t := strings.TrimSpace(p); t != "" {
			out = append(out, t)
		}
	}
	return out
}
