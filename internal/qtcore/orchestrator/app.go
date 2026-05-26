package orchestrator

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"limit-up-sniper-qt/internal/qtcore/config"
	"limit-up-sniper-qt/internal/qtcore/users"
)

type UserManager interface {
	ListFrontendUsers(ctx context.Context) (users.Summary, error)
	CreateFrontendUser(ctx context.Context, req users.CreateRequest) (users.User, error)
	UpdateFrontendUser(ctx context.Context, username string, req users.UpdateRequest) (users.User, error)
	ResetFrontendPassword(ctx context.Context, username string, password string) (users.User, error)
	SetFrontendDisabled(ctx context.Context, username string, disabled bool, reason string) (users.User, error)
	DeleteFrontendUser(ctx context.Context, username string) error
}

type App struct {
	Config      config.Config
	Jobs        JobService
	Users       UserManager
	News        NewsService
	Data        DataService
	Market      MarketService
	UserRuntime UserRuntimeService
	Strategy    StrategyService
}

func New(cfg config.Config, runner JobRunner, userManager UserManager) *App {
	jobs := NewJobService(runner)
	return &App{
		Config:      cfg,
		Jobs:        jobs,
		Users:       userManager,
		News:        NewsService{jobs: jobs},
		Data:        DataService{jobs: jobs},
		Market:      MarketService{jobs: jobs},
		UserRuntime: UserRuntimeService{jobs: jobs},
		Strategy:    StrategyService{jobs: jobs},
	}
}

type StartupRequest struct {
	Date              string `json:"date"`
	StartDate         string `json:"start_date"`
	EndDate           string `json:"end_date"`
	NewsHours         int    `json:"news_hours"`
	NewsPages         int    `json:"news_pages"`
	AIItems           int    `json:"ai_items"`
	MarketCodes       int    `json:"market_codes"`
	Notify            bool   `json:"notify"`
	RunStrategyReplay bool   `json:"run_strategy_replay"`
}

type StepResult struct {
	Name   string         `json:"name"`
	Job    string         `json:"job"`
	Status string         `json:"status"`
	Result map[string]any `json:"result"`
	Error  string         `json:"error,omitempty"`
}

type StartupResult struct {
	Status    string       `json:"status"`
	Message   string       `json:"message"`
	Date      string       `json:"date"`
	StartDate string       `json:"start_date"`
	Steps     []StepResult `json:"steps"`
}

func (a *App) RunJob(ctx context.Context, job string, payload map[string]any) (map[string]any, error) {
	return a.Jobs.Run(ctx, job, payload)
}

func (a *App) Startup(ctx context.Context, req StartupRequest) (StartupResult, error) {
	req = normalizeStartupRequest(req)
	steps := []StepResult{}
	var firstErr error

	runStep := func(name string, job string, fn func(context.Context, map[string]any) (map[string]any, error), payload map[string]any) {
		if ctx.Err() != nil {
			if firstErr == nil {
				firstErr = ctx.Err()
			}
			steps = append(steps, StepResult{Name: name, Job: job, Status: "canceled", Error: ctx.Err().Error()})
			return
		}
		result, err := fn(ctx, payload)
		step := StepResult{Name: name, Job: job, Result: result, Status: resultStatus(result)}
		if err != nil {
			step.Error = err.Error()
			if firstErr == nil {
				firstErr = err
			}
		}
		steps = append(steps, step)
	}

	runStep("news_fetch", JobNewsFetch, a.News.Fetch, map[string]any{
		"hours":          req.NewsHours,
		"pages":          req.NewsPages,
		"page_size":      20,
		"refresh_events": true,
	})
	runStep("ai_analysis", JobAIAnalysis, a.News.Analyze, map[string]any{
		"as_of":      req.Date,
		"max_items":  req.AIItems,
		"batch_size": 4,
	})
	runStep("kline_fill", JobKlineFill, a.Data.FillKline, map[string]any{
		"start_date": req.StartDate,
		"end_date":   req.Date,
		"max_codes":  req.MarketCodes,
		"force":      false,
	})
	runStep("lhb_sync", JobLHBSync, a.Data.SyncLHB, map[string]any{
		"start_date":     req.StartDate,
		"end_date":       req.Date,
		"max_stock_days": req.MarketCodes,
		"force":          false,
		"refresh_events": true,
	})
	runStep("market_sync", JobMarketSync, a.Market.Sync, map[string]any{
		"date":           req.Date,
		"source":         "auto",
		"max_codes":      req.MarketCodes,
		"force":          false,
		"include_latest": true,
	})
	runStep("strategy_daily_refresh", JobStrategyDailyRefresh, a.Strategy.RefreshDaily, map[string]any{
		"date": req.Date,
		"mode": "daily",
	})
	runStep("trade_cycle", JobTradeCycle, a.Strategy.TradeCycle, map[string]any{
		"date":   req.Date,
		"notify": req.Notify,
	})
	if req.RunStrategyReplay {
		runStep("strategy_replay", JobStrategyReplay, a.Strategy.Replay, map[string]any{
			"start_date":     req.StartDate,
			"end_date":       req.Date,
			"mode":           "intraday",
			"batch_days":     15,
			"cursor_enabled": true,
		})
	} else {
		steps = append(steps, StepResult{
			Name:   "strategy_replay",
			Job:    JobStrategyReplay,
			Status: "skipped",
			Result: map[string]any{
				"status":      "skipped",
				"manual_only": true,
				"message":     "strategy replay, training, and backtest are manual-only during startup",
			},
		})
	}

	status := "ok"
	for _, step := range steps {
		if !statusOK(step.Status) {
			status = "partial"
			break
		}
	}
	message := "system startup flow completed"
	if status != "ok" {
		message = "system startup flow completed with failed steps"
	}
	return StartupResult{
		Status:    status,
		Message:   message,
		Date:      req.Date,
		StartDate: req.StartDate,
		Steps:     steps,
	}, firstErr
}

func (a *App) Status(ctx context.Context) (map[string]any, error) {
	_ = ctx
	payload := map[string]any{
		"status":    "ok",
		"generated": time.Now().Format(time.RFC3339),
		"go_control": map[string]any{
			"project_root": a.Config.ProjectRoot,
			"data_dir":     a.Config.DataDir,
			"worker":       a.Config.WorkerScript,
			"python":       a.Config.PythonExecutable,
		},
		"job_zones": JobZones,
	}
	if a.Users != nil {
		summary, err := a.Users.ListFrontendUsers(ctx)
		if err == nil {
			payload["users"] = map[string]any{
				"count":          summary.Count,
				"active_count":   summary.ActiveCount,
				"disabled_count": summary.DisabledCount,
			}
		} else {
			payload["users"] = map[string]any{"status": "error", "message": err.Error()}
		}
	}
	state := readJSON(filepath.Join(a.Config.DataDir, "quant_job_state.json"))
	if state != nil {
		payload["job_state"] = compactJobState(state)
	}
	return payload, nil
}

func normalizeStartupRequest(req StartupRequest) StartupRequest {
	nowDate := time.Now().Format("2006-01-02")
	req.Date = firstNonEmpty(req.EndDate, req.Date, nowDate)
	req.StartDate = firstNonEmpty(req.StartDate, "2026-03-01")
	if req.NewsHours <= 0 {
		req.NewsHours = 24
	}
	if req.NewsPages <= 0 {
		req.NewsPages = 8
	}
	if req.AIItems <= 0 {
		req.AIItems = 20
	}
	if req.MarketCodes <= 0 {
		req.MarketCodes = 200
	}
	return req
}

func resultStatus(result map[string]any) string {
	if result == nil {
		return "unknown"
	}
	if status, ok := result["status"].(string); ok && strings.TrimSpace(status) != "" {
		return strings.TrimSpace(status)
	}
	return "ok"
}

func statusOK(status string) bool {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "ok", "running", "skipped":
		return true
	default:
		return false
	}
}

func readJSON(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil
	}
	return payload
}

func compactJobState(state map[string]any) map[string]any {
	out := map[string]any{}
	for _, key := range []string{"updated_at", "running", "heavy_process_slots", "frontend_runtime_process_slots"} {
		if value, ok := state[key]; ok {
			out[key] = value
		}
	}
	if jobs, ok := state["jobs"].(map[string]any); ok {
		out["jobs"] = compactJobs(jobs)
	}
	return out
}

func compactJobs(jobs map[string]any) map[string]any {
	out := map[string]any{}
	for name, raw := range jobs {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		compact := map[string]any{}
		for _, key := range []string{
			"status",
			"progress_pct",
			"progress_message",
			"last_started_at",
			"last_finished_at",
			"last_error",
			"process",
			"process_pid",
			"stop_requested",
			"success_count",
			"failure_count",
			"skipped_count",
		} {
			if value, exists := item[key]; exists {
				compact[key] = value
			}
		}
		out[name] = compact
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
