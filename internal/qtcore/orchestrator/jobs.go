package orchestrator

import (
	"context"
	"errors"
	"strings"
)

const (
	JobNewsFetch                 = "news_fetch"
	JobAIAnalysis                = "ai_analysis"
	JobMarketSync                = "market_sync"
	JobKlineFill                 = "kline_fill"
	JobLHBSync                   = "lhb_sync"
	JobStrategyDailyRefresh      = "strategy_daily_refresh"
	JobTradeCycle                = "trade_cycle"
	JobFrontendAccountPrecompute = "frontend_account_precompute"
	JobFrontendPayloadPrecompute = "frontend_payload_precompute"
	JobStrategyReplay            = "strategy_replay"
	JobStrategyEvolution         = "strategy_evolution"
	JobModelBacktest             = "model_backtest"
	JobQuantTimeline             = "quant_timeline"
	JobQuantBacktest             = "quant_backtest"
	JobFitStrategy               = "fit_strategy"
	JobDataCoverage              = "data_coverage"
	JobSystemStartup             = "system_startup"
)

var JobZones = map[string]string{
	JobNewsFetch:                 "data_collection",
	JobAIAnalysis:                "data_collection",
	JobMarketSync:                "market_data",
	JobKlineFill:                 "market_data",
	JobLHBSync:                   "data_collection",
	JobStrategyDailyRefresh:      "daily_strategy",
	JobTradeCycle:                "daily_strategy",
	JobFrontendAccountPrecompute: "user_runtime",
	JobFrontendPayloadPrecompute: "frontend_cache",
	JobStrategyReplay:            "research",
	JobStrategyEvolution:         "research",
	JobModelBacktest:             "research",
	JobQuantTimeline:             "research",
	JobQuantBacktest:             "research",
	JobFitStrategy:               "research",
	JobDataCoverage:              "diagnostic",
	JobSystemStartup:             "system",
}

type JobRunner interface {
	Run(ctx context.Context, job string, payload map[string]any) (map[string]any, error)
}

type JobService struct {
	runner JobRunner
}

func NewJobService(runner JobRunner) JobService {
	return JobService{runner: runner}
}

func (s JobService) Run(ctx context.Context, job string, payload map[string]any) (map[string]any, error) {
	job = strings.TrimSpace(job)
	if _, ok := JobZones[job]; !ok {
		return nil, errors.New("unsupported job: " + job)
	}
	return s.runner.Run(ctx, job, payload)
}

type NewsService struct {
	jobs JobService
}

func (s NewsService) Fetch(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobNewsFetch, payload)
}

func (s NewsService) Analyze(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobAIAnalysis, payload)
}

type DataService struct {
	jobs JobService
}

func (s DataService) Coverage(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobDataCoverage, payload)
}

func (s DataService) FillKline(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobKlineFill, payload)
}

func (s DataService) SyncLHB(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobLHBSync, payload)
}

type MarketService struct {
	jobs JobService
}

func (s MarketService) Sync(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobMarketSync, payload)
}

type UserRuntimeService struct {
	jobs JobService
}

func (s UserRuntimeService) PrecomputeAccounts(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobFrontendAccountPrecompute, payload)
}

func (s UserRuntimeService) PrecomputeFrontendPayload(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobFrontendPayloadPrecompute, payload)
}

type StrategyService struct {
	jobs JobService
}

func (s StrategyService) RefreshDaily(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobStrategyDailyRefresh, payload)
}

func (s StrategyService) TradeCycle(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobTradeCycle, payload)
}

func (s StrategyService) Replay(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobStrategyReplay, payload)
}

func (s StrategyService) Evolve(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobStrategyEvolution, payload)
}

func (s StrategyService) ModelBacktest(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobModelBacktest, payload)
}

func (s StrategyService) Timeline(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobQuantTimeline, payload)
}

func (s StrategyService) Backtest(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobQuantBacktest, payload)
}

func (s StrategyService) Fit(ctx context.Context, payload map[string]any) (map[string]any, error) {
	return s.jobs.Run(ctx, JobFitStrategy, payload)
}
