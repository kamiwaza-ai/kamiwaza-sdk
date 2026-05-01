// Reference Go implementation of the Kamiwaza non-SDK extension contract.
// See kamiwaza-sdk/docs/extensions/non-sdk-flow.md (canonical) and §4.2.11
// of the D210 system design.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/kamiwaza/kamiwaza-sdk/examples/extensions/go-reference/internal/identity"
)

// shutdownGrace is the upper bound on the SIGTERM drain mandated by the
// runtime contract (non-sdk-flow.md §1: "respond to SIGTERM with a graceful
// shutdown drain (≤ 30s)").
const shutdownGrace = 30 * time.Second

// maxBodyBytes caps request bodies on tool endpoints. The trust boundary is
// Traefik (non-sdk-flow.md §8) but a forged in-cluster caller could still
// hit the extension directly until the Istio follow-on lands; bound the
// JSON decode to keep that failure mode local.
const maxBodyBytes int64 = 1 << 20 // 1 MiB

func main() {
	logger := newLogger(getenv("LOG_LEVEL", "info"))
	slog.SetDefault(logger)

	useAuth := getenv("KAMIWAZA_USE_AUTH", "false") == "true"
	port := getenv("PORT", "8000")

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.Handle("/tools/echo", identityMiddleware(useAuth, http.HandlerFunc(handleEcho)))

	// Timeouts defend against slowloris and stalled clients. A reference
	// extension is held up to other authors as the canonical shape — omit
	// these and the omission propagates into production code.
	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// SIGTERM/SIGINT trigger a graceful shutdown. ListenAndServe runs in a
	// goroutine so the signal handler can call Shutdown on the same Server.
	errs := make(chan error, 1)
	go func() {
		logger.Info("listening", "addr", srv.Addr, "use_auth", useAuth)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
		close(errs)
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	select {
	case sig := <-stop:
		logger.Info("shutdown signal received", "signal", sig.String())
	case err := <-errs:
		if err != nil {
			logger.Error("listener error", "err", err)
			os.Exit(1)
		}
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		logger.Error("graceful shutdown failed", "err", err)
		os.Exit(1)
	}
}

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleEcho reads the parsed Identity from context and echoes a payload back.
// Demonstrates the contract end-to-end: middleware → identity → tool handler.
func handleEcho(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		// 405 is HTTP-protocol-level and not in flow doc §5's class set;
		// "bad_request" is an application-level class outside the
		// canonical platform-failure list (which only covers envelope /
		// trust-boundary failures).
		writeError(w, http.StatusMethodNotAllowed, "bad_request", "method not allowed")
		return
	}
	var body struct {
		Message string `json:"message"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid JSON body")
		return
	}
	id, _ := r.Context().Value(identityCtxKey{}).(*identity.Identity)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"identity": id,
		"echo":     body.Message,
	})
}

// identityCtxKey is the request-scope key under which a parsed Identity is
// stored. Unexported so callers can't fish it out from outside this package.
type identityCtxKey struct{}

// identityMiddleware parses the envelope on every request when KAMIWAZA_USE_AUTH
// is true. On parse failure, returns the canonical 401/misbound_auth response
// shape from non-sdk-flow.md §5. When false (local dev), the handler runs
// without an Identity in context.
func identityMiddleware(useAuth bool, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !useAuth {
			next.ServeHTTP(w, r)
			return
		}
		id, err := identity.Extract(r.Header)
		if err != nil {
			var mb *identity.MisboundAuthError
			if errors.As(err, &mb) {
				writeError(w, http.StatusUnauthorized, "misbound_auth", mb.Error())
				return
			}
			// Defensive: Extract today returns only nil or *MisboundAuthError.
			// This branch surfaces any future error type as a generic 500 so
			// adding a new error class without classifying it here is loud.
			writeError(w, http.StatusInternalServerError, "kamiwaza_runtime_error", "identity extraction failed")
			return
		}
		ctx := context.WithValue(r.Context(), identityCtxKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func writeJSON(w http.ResponseWriter, status int, body interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// writeError emits the canonical failure JSON: {"error":{"class","message"}}.
// Never include stack traces, internal hostnames, or upstream response bodies
// (non-sdk-flow.md §5).
func writeError(w http.ResponseWriter, status int, class, message string) {
	writeJSON(w, status, map[string]interface{}{
		"error": map[string]string{"class": class, "message": message},
	})
}

func getenv(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

func newLogger(level string) *slog.Logger {
	var lvl slog.Level
	switch strings.ToLower(level) {
	case "debug":
		lvl = slog.LevelDebug
	case "warn", "warning":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: lvl}))
}
