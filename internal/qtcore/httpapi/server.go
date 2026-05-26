package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"limit-up-sniper-qt/internal/qtcore/orchestrator"
	"limit-up-sniper-qt/internal/qtcore/users"
)

type Server struct {
	App          *orchestrator.App
	ControlToken string
}

func (s Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.wrap(s.health))
	mux.HandleFunc("GET /api/go/status", s.wrap(s.status))
	mux.HandleFunc("POST /api/go/startup", s.wrap(s.startup))
	mux.HandleFunc("POST /api/go/jobs/", s.wrap(s.runJob))
	mux.HandleFunc("GET /api/go/users", s.wrap(s.listUsers))
	mux.HandleFunc("POST /api/go/users", s.wrap(s.createUser))
	mux.HandleFunc("PATCH /api/go/users/", s.wrap(s.updateUser))
	mux.HandleFunc("DELETE /api/go/users/", s.wrap(s.deleteUser))
	mux.HandleFunc("POST /api/go/users/", s.wrap(s.userAction))
	return mux
}

type handlerFunc func(http.ResponseWriter, *http.Request) (any, int, error)

func (s Server) wrap(next handlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !s.authorized(r) {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"status": "error", "message": "unauthorized"})
			return
		}
		payload, status, err := next(w, r)
		if err != nil {
			writeJSON(w, statusOr(status, http.StatusBadRequest), map[string]any{
				"status":  "error",
				"message": err.Error(),
			})
			return
		}
		writeJSON(w, statusOr(status, http.StatusOK), payload)
	}
}

func (s Server) authorized(r *http.Request) bool {
	token := strings.TrimSpace(s.ControlToken)
	if token == "" {
		return true
	}
	actual := strings.TrimSpace(r.Header.Get("x-qt-control-token"))
	if actual == "" {
		actual = strings.TrimSpace(r.URL.Query().Get("control_token"))
	}
	return subtle.ConstantTimeCompare([]byte(actual), []byte(token)) == 1
}

func (s Server) health(_ http.ResponseWriter, _ *http.Request) (any, int, error) {
	return map[string]any{"status": "ok", "service": "qt-go-control"}, http.StatusOK, nil
}

func (s Server) status(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	payload, err := s.App.Status(r.Context())
	return payload, http.StatusOK, err
}

func (s Server) startup(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	var req orchestrator.StartupRequest
	if err := decodeJSON(r, &req); err != nil {
		return nil, http.StatusBadRequest, err
	}
	ctx, cancel := requestContext(r)
	defer cancel()
	result, err := s.App.Startup(ctx, req)
	status := http.StatusOK
	if err != nil {
		status = http.StatusAccepted
	}
	return result, status, nil
}

func (s Server) runJob(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	job := strings.TrimPrefix(r.URL.Path, "/api/go/jobs/")
	job = strings.Trim(job, "/")
	var payload map[string]any
	if err := decodeJSON(r, &payload); err != nil {
		return nil, http.StatusBadRequest, err
	}
	ctx, cancel := requestContext(r)
	defer cancel()
	result, err := s.App.RunJob(ctx, job, payload)
	if err != nil {
		if result == nil {
			return nil, http.StatusBadRequest, err
		}
		return result, http.StatusAccepted, nil
	}
	return result, http.StatusOK, nil
}

func (s Server) listUsers(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	payload, err := s.App.Users.ListFrontendUsers(r.Context())
	return payload, http.StatusOK, err
}

func (s Server) createUser(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	var req users.CreateRequest
	if err := decodeJSON(r, &req); err != nil {
		return nil, http.StatusBadRequest, err
	}
	user, err := s.App.Users.CreateFrontendUser(r.Context(), req)
	return map[string]any{"status": "ok", "user": user}, http.StatusCreated, err
}

func (s Server) updateUser(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	username := userPathValue(r.URL.Path)
	var req users.UpdateRequest
	if err := decodeJSON(r, &req); err != nil {
		return nil, http.StatusBadRequest, err
	}
	user, err := s.App.Users.UpdateFrontendUser(r.Context(), username, req)
	return map[string]any{"status": "ok", "user": user}, http.StatusOK, err
}

func (s Server) deleteUser(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	username := userPathValue(r.URL.Path)
	err := s.App.Users.DeleteFrontendUser(r.Context(), username)
	return map[string]any{"status": "ok", "deleted": username}, http.StatusOK, err
}

func (s Server) userAction(_ http.ResponseWriter, r *http.Request) (any, int, error) {
	username, action := splitUserAction(r.URL.Path)
	switch action {
	case "password":
		var payload struct {
			Password string `json:"password"`
		}
		if err := decodeJSON(r, &payload); err != nil {
			return nil, http.StatusBadRequest, err
		}
		user, err := s.App.Users.ResetFrontendPassword(r.Context(), username, payload.Password)
		return map[string]any{"status": "ok", "user": user}, http.StatusOK, err
	case "ban":
		var payload struct {
			Reason string `json:"reason"`
		}
		_ = decodeJSON(r, &payload)
		user, err := s.App.Users.SetFrontendDisabled(r.Context(), username, true, payload.Reason)
		return map[string]any{"status": "ok", "user": user}, http.StatusOK, err
	case "unban":
		user, err := s.App.Users.SetFrontendDisabled(r.Context(), username, false, "")
		return map[string]any{"status": "ok", "user": user}, http.StatusOK, err
	default:
		return nil, http.StatusNotFound, errors.New("unknown user action")
	}
}

func decodeJSON(r *http.Request, target any) error {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	decoder := json.NewDecoder(r.Body)
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		if err.Error() == "EOF" {
			return nil
		}
		return err
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("content-type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(payload)
}

func requestContext(r *http.Request) (context.Context, context.CancelFunc) {
	timeout := 2 * time.Hour
	return context.WithTimeout(r.Context(), timeout)
}

func userPathValue(path string) string {
	rest := strings.TrimPrefix(path, "/api/go/users/")
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 0 {
		return ""
	}
	return parts[0]
}

func splitUserAction(path string) (string, string) {
	rest := strings.TrimPrefix(path, "/api/go/users/")
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) < 2 {
		return userPathValue(path), ""
	}
	return parts[0], parts[1]
}

func statusOr(status int, fallback int) int {
	if status > 0 {
		return status
	}
	return fallback
}
