from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


FitStrategyPayload = Callable[[Optional[str], Optional[str], Optional[str], bool, bool, bool, bool], Dict[str, Any]]
EvolutionStatusPayload = Callable[[], Dict[str, Any]]
EvolutionTracePayload = Callable[[Optional[str], Optional[int], int], Dict[str, Any]]
SimplePayload = Callable[[], Dict[str, Any]]
ModelsPayload = Callable[[], Dict[str, Any]]
ModelBacktestPayload = Callable[[str, Optional[str], Optional[str], str, int, bool, bool, bool, bool, bool], Dict[str, Any]]
ApplyModelPayload = Callable[[str], Dict[str, Any]]
EvolveStrategyPayload = Callable[[int, int, Optional[str], Optional[str], bool, str, bool, bool], Dict[str, Any]]


def build_quant_strategy_router(
    *,
    fit_strategy_payload: FitStrategyPayload,
    evolution_status_payload: EvolutionStatusPayload,
    evolution_trace_payload: EvolutionTracePayload,
    evolution_pause_payload: SimplePayload,
    evolution_resume_payload: SimplePayload,
    models_payload: ModelsPayload,
    model_backtest_payload: ModelBacktestPayload,
    apply_model_payload: ApplyModelPayload,
    evolve_strategy_payload: EvolveStrategyPayload,
    fit_strategy_defer_default: bool,
    fit_strategy_process_default: bool,
    model_backtest_defer_default: bool,
    model_backtest_process_default: bool,
    evolve_process_default: bool,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/quant/fit_strategy")
    def quant_fit_strategy(
        as_of: Optional[str] = Query(default=None),
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        apply_best: bool = Query(default=True),
        defer: bool = Query(default=fit_strategy_defer_default),
        process: bool = Query(default=fit_strategy_process_default),
        manual: bool = Query(default=False),
    ):
        return fit_strategy_payload(as_of, start_date, end_date, apply_best, defer, process, manual)

    @router.get("/api/quant/evolution/status")
    def quant_evolution_status():
        return evolution_status_payload()

    @router.get("/api/quant/evolution/trace")
    def quant_evolution_trace(
        run_id: Optional[str] = Query(default=None),
        generation: Optional[int] = Query(default=None, ge=1),
        limit: int = Query(default=200, ge=1, le=2000),
    ):
        return evolution_trace_payload(run_id, generation, limit)

    @router.post("/api/quant/evolution/pause")
    def quant_pause_evolution():
        return evolution_pause_payload()

    @router.post("/api/quant/evolution/resume")
    def quant_resume_evolution():
        return evolution_resume_payload()

    @router.get("/api/quant/models")
    def quant_strategy_models():
        return models_payload()

    @router.get("/api/quant/model/backtest")
    def quant_strategy_model_backtest(
        model_id: str = Query(default="active"),
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        mode: str = Query(default="intraday"),
        limit: int = Query(default=0, ge=0, le=5000),
        recompute: bool = Query(default=False),
        force: bool = Query(default=False),
        defer: bool = Query(default=model_backtest_defer_default),
        manual: bool = Query(default=False),
        process: bool = Query(default=model_backtest_process_default),
    ):
        return model_backtest_payload(
            model_id,
            start_date,
            end_date,
            mode,
            limit,
            recompute,
            force,
            defer,
            manual,
            process,
        )

    @router.post("/api/quant/model/apply")
    def quant_apply_strategy_model(model_id: str = Query(...)):
        return apply_model_payload(model_id)

    @router.post("/api/quant/evolve_strategy")
    def quant_evolve_strategy(
        generations: int = Query(default=4, ge=1, le=30),
        population_size: int = Query(default=16, ge=6, le=80),
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        apply_best: bool = Query(default=False),
        mode: str = Query(default="intraday"),
        background: bool = Query(default=True),
        process: bool = Query(default=evolve_process_default),
    ):
        return evolve_strategy_payload(
            generations,
            population_size,
            start_date,
            end_date,
            apply_best,
            mode,
            background,
            process,
        )

    return router
