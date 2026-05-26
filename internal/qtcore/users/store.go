package users

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	defaultCash       = 10000.0
	defaultStrategyID = "capital_10000"
	pbkdf2Iterations  = 200000
)

type Store struct {
	Path string
	Now  func() time.Time
}

type User struct {
	Username            string         `json:"username"`
	CreatedAt           string         `json:"created_at"`
	LastLoginAt         string         `json:"last_login_at"`
	LoginCount          int            `json:"login_count"`
	FailedLoginCount    int            `json:"failed_login_count"`
	LastFailedLoginAt   string         `json:"last_failed_login_at"`
	RegisteredIP        string         `json:"registered_ip"`
	LastLoginIP         string         `json:"last_login_ip"`
	Profile             map[string]any `json:"profile"`
	ProfileUpdatedAt    string         `json:"profile_updated_at"`
	Disabled            bool           `json:"disabled"`
	DisabledAt          string         `json:"disabled_at"`
	DisabledReason      string         `json:"disabled_reason"`
	CredentialUpdatedAt string         `json:"credential_updated_at"`
	HasCredential       bool           `json:"has_credential"`
	Source              string         `json:"source"`
	RegisteredUserAgent string         `json:"registered_user_agent,omitempty"`
	LastLoginUserAgent  string         `json:"last_login_user_agent,omitempty"`
}

type Summary struct {
	Status        string `json:"status"`
	Items         []User `json:"items"`
	Count         int    `json:"count"`
	ActiveCount   int    `json:"active_count"`
	DisabledCount int    `json:"disabled_count"`
}

type CreateRequest struct {
	Username string         `json:"username"`
	Password string         `json:"password"`
	Profile  map[string]any `json:"profile"`
}

type UpdateRequest struct {
	Profile map[string]any `json:"profile"`
}

func (s Store) ListFrontendUsers(ctx context.Context) (Summary, error) {
	_ = ctx
	root, err := s.load()
	if err != nil {
		return Summary{}, err
	}
	frontendUsers := ensureFrontendUsers(root)
	items := make([]User, 0, len(frontendUsers))
	for username, raw := range frontendUsers {
		record, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		items = append(items, publicUser(username, record, "frontend_users"))
	}
	sort.Slice(items, func(i, j int) bool {
		left := firstNonEmpty(items[i].LastLoginAt, items[i].CreatedAt)
		right := firstNonEmpty(items[j].LastLoginAt, items[j].CreatedAt)
		return left > right
	})
	disabled := 0
	for _, item := range items {
		if item.Disabled {
			disabled++
		}
	}
	return Summary{
		Status:        "ok",
		Items:         items,
		Count:         len(items),
		ActiveCount:   len(items) - disabled,
		DisabledCount: disabled,
	}, nil
}

func (s Store) CreateFrontendUser(ctx context.Context, req CreateRequest) (User, error) {
	_ = ctx
	username := strings.TrimSpace(req.Username)
	if len(username) < 3 {
		return User{}, errors.New("username must be at least 3 characters")
	}
	if len(req.Password) < 6 {
		return User{}, errors.New("password must be at least 6 characters")
	}
	root, err := s.load()
	if err != nil {
		return User{}, err
	}
	now := s.nowISO()
	root["version"] = float64(2)
	if strings.TrimSpace(asString(root["created_at"])) == "" {
		root["created_at"] = now
	}
	if strings.TrimSpace(asString(root["token_secret"])) == "" {
		secret, err := tokenSecret()
		if err != nil {
			return User{}, err
		}
		root["token_secret"] = secret
	}
	frontendUsers := ensureFrontendUsers(root)
	if _, exists := frontendUsers[username]; exists {
		return User{}, fmt.Errorf("frontend user %q already exists", username)
	}
	password, err := hashPassword(req.Password)
	if err != nil {
		return User{}, err
	}
	record := map[string]any{
		"username":      username,
		"password":      password,
		"created_at":    now,
		"created_by":    "go_control",
		"last_login_at": "",
		"login_count":   float64(0),
		"profile":       normalizeProfile(mergeProfile(req.Profile, map[string]any{"follow_started_at": now})),
	}
	frontendUsers[username] = record
	root["updated_at"] = now
	if err := s.save(root); err != nil {
		return User{}, err
	}
	return publicUser(username, record, "frontend_users"), nil
}

func (s Store) UpdateFrontendUser(ctx context.Context, username string, req UpdateRequest) (User, error) {
	_ = ctx
	username = strings.TrimSpace(username)
	root, err := s.load()
	if err != nil {
		return User{}, err
	}
	frontendUsers := ensureFrontendUsers(root)
	record, ok := frontendUsers[username].(map[string]any)
	if !ok {
		return User{}, fmt.Errorf("frontend user %q not found", username)
	}
	current, _ := record["profile"].(map[string]any)
	merged := mergeProfile(current, req.Profile)
	oldModel := asString(current["strategy_model_id"])
	newModel := firstNonEmpty(asString(merged["strategy_model_id"]), oldModel)
	oldCash := safeFloat(current["simulated_cash"], defaultCash)
	newCash := safeFloat(merged["simulated_cash"], oldCash)
	if (newModel != "" && newModel != oldModel) || math.Abs(newCash-oldCash) >= 0.01 {
		now := s.nowISO()
		merged["follow_started_at"] = now
		merged["follow_start_date"] = datePrefix(now)
	}
	if strings.TrimSpace(asString(merged["follow_started_at"])) == "" {
		merged["follow_started_at"] = firstNonEmpty(asString(record["created_at"]), s.nowISO())
	}
	record["profile"] = normalizeProfile(merged)
	record["profile_updated_at"] = s.nowISO()
	root["updated_at"] = s.nowISO()
	if err := s.save(root); err != nil {
		return User{}, err
	}
	return publicUser(username, record, "frontend_users"), nil
}

func (s Store) ResetFrontendPassword(ctx context.Context, username string, password string) (User, error) {
	_ = ctx
	username = strings.TrimSpace(username)
	if len(password) < 6 {
		return User{}, errors.New("password must be at least 6 characters")
	}
	root, err := s.load()
	if err != nil {
		return User{}, err
	}
	frontendUsers := ensureFrontendUsers(root)
	record, ok := frontendUsers[username].(map[string]any)
	if !ok {
		return User{}, fmt.Errorf("frontend user %q not found", username)
	}
	hash, err := hashPassword(password)
	if err != nil {
		return User{}, err
	}
	now := s.nowISO()
	record["password"] = hash
	record["password_updated_at"] = now
	record["failed_login_count"] = float64(0)
	root["updated_at"] = now
	if err := s.save(root); err != nil {
		return User{}, err
	}
	return publicUser(username, record, "frontend_users"), nil
}

func (s Store) SetFrontendDisabled(ctx context.Context, username string, disabled bool, reason string) (User, error) {
	_ = ctx
	username = strings.TrimSpace(username)
	root, err := s.load()
	if err != nil {
		return User{}, err
	}
	frontendUsers := ensureFrontendUsers(root)
	record, ok := frontendUsers[username].(map[string]any)
	if !ok {
		return User{}, fmt.Errorf("frontend user %q not found", username)
	}
	now := s.nowISO()
	record["disabled"] = disabled
	if disabled {
		record["disabled_at"] = now
		record["disabled_reason"] = truncate(strings.TrimSpace(firstNonEmpty(reason, "disabled by go control")), 200)
	} else {
		record["disabled_at"] = ""
		record["disabled_reason"] = ""
	}
	root["updated_at"] = now
	if err := s.save(root); err != nil {
		return User{}, err
	}
	return publicUser(username, record, "frontend_users"), nil
}

func (s Store) DeleteFrontendUser(ctx context.Context, username string) error {
	_ = ctx
	username = strings.TrimSpace(username)
	root, err := s.load()
	if err != nil {
		return err
	}
	frontendUsers := ensureFrontendUsers(root)
	if _, exists := frontendUsers[username]; !exists {
		return fmt.Errorf("frontend user %q not found", username)
	}
	delete(frontendUsers, username)
	root["updated_at"] = s.nowISO()
	return s.save(root)
}

func (s Store) load() (map[string]any, error) {
	if strings.TrimSpace(s.Path) == "" {
		return nil, errors.New("auth path is empty")
	}
	data, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return map[string]any{}, nil
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	if payload == nil {
		payload = map[string]any{}
	}
	return payload, nil
}

func (s Store) save(payload map[string]any) error {
	if err := os.MkdirAll(filepath.Dir(s.Path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(s.Path, data, 0o600)
}

func (s Store) nowISO() string {
	now := time.Now()
	if s.Now != nil {
		now = s.Now()
	}
	return now.Format("2006-01-02T15:04:05")
}

func ensureFrontendUsers(root map[string]any) map[string]any {
	users, _ := root["users"].(map[string]any)
	if users == nil {
		users = map[string]any{}
		root["users"] = users
	}
	frontendUsers, _ := users["frontend_users"].(map[string]any)
	if frontendUsers == nil {
		frontendUsers = map[string]any{}
		users["frontend_users"] = frontendUsers
	}
	legacy, _ := users["frontend"].(map[string]any)
	legacyUsername := strings.TrimSpace(asString(legacy["username"]))
	if legacyUsername != "" {
		if _, exists := frontendUsers[legacyUsername]; !exists {
			clone := cloneMap(legacy)
			clone["username"] = legacyUsername
			frontendUsers[legacyUsername] = clone
		}
	}
	return frontendUsers
}

func publicUser(username string, record map[string]any, source string) User {
	profile, _ := record["profile"].(map[string]any)
	return User{
		Username:            firstNonEmpty(asString(record["username"]), username),
		CreatedAt:           asString(record["created_at"]),
		LastLoginAt:         asString(record["last_login_at"]),
		LoginCount:          int(safeFloat(record["login_count"], 0)),
		FailedLoginCount:    int(safeFloat(record["failed_login_count"], 0)),
		LastFailedLoginAt:   asString(record["last_failed_login_at"]),
		RegisteredIP:        asString(record["registered_ip"]),
		RegisteredUserAgent: asString(record["registered_user_agent"]),
		LastLoginIP:         asString(record["last_login_ip"]),
		LastLoginUserAgent:  asString(record["last_login_user_agent"]),
		Profile:             normalizeProfile(profile),
		ProfileUpdatedAt:    asString(record["profile_updated_at"]),
		Disabled:            asBool(record["disabled"]),
		DisabledAt:          asString(record["disabled_at"]),
		DisabledReason:      asString(record["disabled_reason"]),
		CredentialUpdatedAt: asString(record["password_updated_at"]),
		HasCredential:       record["password"] != nil,
		Source:              source,
	}
}

func normalizeProfile(raw map[string]any) map[string]any {
	cash := safeFloat(raw["simulated_cash"], defaultCash)
	if cash < 10000 {
		cash = 10000
	}
	if cash > 10000000 {
		cash = 10000000
	}
	modelID := truncate(firstNonEmpty(asString(raw["strategy_model_id"]), defaultStrategyID), 120)
	followStartedAt := strings.TrimSpace(firstNonEmpty(asString(raw["follow_started_at"]), asString(raw["created_at"])))
	followStartDate := datePrefix(firstNonEmpty(asString(raw["follow_start_date"]), datePrefix(followStartedAt)))
	return map[string]any{
		"simulated_cash":    math.Round(cash*100) / 100,
		"strategy_model_id": modelID,
		"follow_started_at": followStartedAt,
		"follow_start_date": followStartDate,
	}
}

func mergeProfile(left map[string]any, right map[string]any) map[string]any {
	merged := cloneMap(left)
	for key, value := range right {
		merged[key] = value
	}
	return merged
}

func hashPassword(password string) (map[string]any, error) {
	saltBytes := make([]byte, 16)
	if _, err := rand.Read(saltBytes); err != nil {
		return nil, err
	}
	salt := hex.EncodeToString(saltBytes)
	digest := pbkdf2SHA256([]byte(password), []byte(salt), pbkdf2Iterations, sha256.Size)
	return map[string]any{
		"algorithm":  "pbkdf2_sha256",
		"iterations": float64(pbkdf2Iterations),
		"salt":       salt,
		"hash":       hex.EncodeToString(digest),
	}, nil
}

func pbkdf2SHA256(password []byte, salt []byte, iterations int, keyLen int) []byte {
	if iterations <= 0 {
		iterations = 1
	}
	hashLen := sha256.Size
	blockCount := (keyLen + hashLen - 1) / hashLen
	output := make([]byte, 0, blockCount*hashLen)
	for block := 1; block <= blockCount; block++ {
		mac := hmac.New(sha256.New, password)
		mac.Write(salt)
		var blockBytes [4]byte
		binary.BigEndian.PutUint32(blockBytes[:], uint32(block))
		mac.Write(blockBytes[:])
		u := mac.Sum(nil)
		t := append([]byte(nil), u...)
		for i := 1; i < iterations; i++ {
			mac = hmac.New(sha256.New, password)
			mac.Write(u)
			u = mac.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		output = append(output, t...)
	}
	return output[:keyLen]
}

func tokenSecret() (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return strings.TrimRight(base64.URLEncoding.EncodeToString(raw), "="), nil
}

func safeFloat(value any, fallback float64) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case float32:
		return float64(typed)
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	case json.Number:
		parsed, err := typed.Float64()
		if err == nil {
			return parsed
		}
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(typed), 64)
		if err == nil {
			return parsed
		}
	}
	return fallback
}

func asString(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case fmt.Stringer:
		return typed.String()
	case nil:
		return ""
	default:
		return fmt.Sprint(typed)
	}
}

func asBool(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		switch strings.ToLower(strings.TrimSpace(typed)) {
		case "1", "true", "yes", "on":
			return true
		}
	}
	return false
}

func cloneMap(in map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range in {
		out[key] = value
	}
	return out
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func datePrefix(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 10 {
		return value[:10]
	}
	return value
}

func truncate(value string, maxLen int) string {
	if maxLen <= 0 {
		return ""
	}
	runes := []rune(value)
	if len(runes) <= maxLen {
		return value
	}
	return string(runes[:maxLen])
}
