# 作者：相空
"""
回测 API
ADR-3: equity_curve / trades 存为 JSON (单用户可接受)
"""
import json
import math
from collections import Counter
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..db import get_db
from ..engine.backtest import (
    TRADE_STAT_KEYS,
    backtest_sector_rotation,
    prepare_backtest_context,
    summarize_backtest_result,
)
from ..engine.data import get_benchmark_data, get_etf_data
from ..engine.universe import SECTOR_MAP
from ..models import (
    BacktestDrilldownParams,
    BacktestParams,
    BacktestScan2DParams,
    BacktestScanParams,
    BacktestWalkForwardParams,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

SCAN_PARAM_SPECS = {
    "top_n": {"label": "Top N", "kind": "int", "min": 1, "max": 20},
    "min_hold": {"label": "最短持有天数", "kind": "int", "min": 0, "max": 120},
    "rebal_days": {"label": "调仓周期", "kind": "int", "min": 1, "max": 60},
    "max_offensive": {"label": "进攻上限", "kind": "int", "min": 1, "max": 10},
    "consec_down_exit": {"label": "连跌退出天数", "kind": "int", "min": 0, "max": 15},
    "backtest_years": {"label": "回测年数", "kind": "int", "min": 1, "max": 20},
    "rank_offset": {"label": "排名偏移", "kind": "int", "min": 0, "max": 10},
    "score_mode": {
        "label": "评分模式",
        "kind": "enum",
        "choices": ["rank_momentum", "mixed", "pure20"],
    },
    "ma200_risk": {"label": "MA200风控", "kind": "bool"},
    "spike_filter": {"label": "Spike过滤", "kind": "bool"},
    "weight_scheme": {
        "label": "权重模式",
        "kind": "enum",
        "choices": ["equal", "momentum", "inv_vol"],
    },
}
SCANNABLE_PARAMS = set(SCAN_PARAM_SPECS)
DEFAULT_WALK_FORWARD_OBJECTIVE = "sharpe"
DEFAULT_WALK_FORWARD_WINDOW = "rolling"


def _sanitize_metrics(metrics: dict | None) -> dict | None:
    if not metrics:
        return metrics
    sanitized = {}
    for key, value in metrics.items():
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            sanitized[key] = None
        else:
            sanitized[key] = value
    return sanitized


def _extract_trade_stats(metrics: dict | None) -> dict:
    metrics = metrics or {}
    return {key: metrics.get(key) for key in TRADE_STAT_KEYS}


def _sample_equity_curve(eq: list[dict], limit: int = 500) -> list[dict]:
    if len(eq) <= limit:
        return eq
    step = math.ceil(len(eq) / limit)
    sampled = eq[::step]
    if sampled and eq and sampled[-1] != eq[-1]:
        sampled.append(eq[-1])
    return sampled


def _map_equity_curve(eq: list[dict], initial_capital: float) -> list[dict]:
    return [
        {
            "date": pt["date"],
            "value": round(float(pt["nav"]) * initial_capital, 2),
            "drawdown": pt.get("drawdown", 0),
        }
        for pt in eq
    ]


def _prepare_api_result(
    result: dict,
    initial_capital: float,
    *,
    sample_limit: int | None = None,
    include_trades: bool = True,
    include_yearly: bool = True,
    include_monthly: bool = True,
) -> dict:
    metrics = _sanitize_metrics(result.get("metrics")) or {}
    eq = result.get("equity_curve", []) or []
    if sample_limit:
        eq = _sample_equity_curve(eq, sample_limit)
    payload = {
        "metrics": metrics,
        "equity_curve": _map_equity_curve(eq, initial_capital),
        "trade_stats": _extract_trade_stats(metrics),
    }
    if include_trades:
        payload["trades"] = result.get("trades", [])
    if include_yearly:
        payload["yearly"] = result.get("yearly", {})
    if include_monthly:
        payload["monthly"] = result.get("monthly", {})
    return payload


def _current_end_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _current_start_date(years: int) -> str:
    start_year = datetime.now().year - years
    return f"{start_year}-01-01"


def _load_market_context(max_years: int) -> dict:
    start_year = datetime.now().year - max_years
    data_start = f"{start_year - 1}-01-01"
    end_date = _current_end_date()
    codes = list(SECTOR_MAP.values())
    etf_data = get_etf_data(codes, data_start, end_date)
    benchmark = get_benchmark_data(data_start, end_date)
    if not etf_data:
        raise HTTPException(500, "无法获取回测数据")
    context = prepare_backtest_context(etf_data, benchmark)
    return {"etf_data": etf_data, "benchmark": benchmark, "context": context}


def _coerce_scan_value(param_name: str, value: Any):
    spec = SCAN_PARAM_SPECS[param_name]
    kind = spec["kind"]
    if kind == "int":
        if isinstance(value, bool):
            raise HTTPException(400, f"{param_name} 需要整数值")
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError as exc:
                raise HTTPException(400, f"{param_name} 需要整数值") from exc
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise HTTPException(400, f"{param_name} 需要整数值")
        if not float(value).is_integer():
            raise HTTPException(400, f"{param_name} 需要整数值")
        iv = int(value)
        if iv < spec["min"] or iv > spec["max"]:
            raise HTTPException(400, f"{param_name} 超出允许范围 [{spec['min']}, {spec['max']}]")
        return iv
    if kind == "enum":
        if not isinstance(value, str):
            raise HTTPException(400, f"{param_name} 需要字符串枚举值")
        sv = value.strip()
        if sv not in spec["choices"]:
            raise HTTPException(400, f"{param_name} 不支持值: {value}")
        return sv
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            s = value.strip().lower()
            if s in {"true", "1", "on", "yes"}:
                return True
            if s in {"false", "0", "off", "no"}:
                return False
        raise HTTPException(400, f"{param_name} 需要布尔值")
    raise HTTPException(400, f"不支持扫描参数: {param_name}")


def _normalize_scan_values(param_name: str, values: list[Any], *, limit: int) -> list[Any]:
    if param_name not in SCANNABLE_PARAMS:
        raise HTTPException(400, f"不支持扫描参数: {param_name}, 可选: {sorted(SCANNABLE_PARAMS)}")
    if len(values) > limit:
        raise HTTPException(400, f"最多允许 {limit} 个扫描值")

    normalized = []
    seen = set()
    for value in values:
        normalized_value = _coerce_scan_value(param_name, value)
        token = json.dumps(normalized_value, ensure_ascii=False, sort_keys=True)
        if token not in seen:
            normalized.append(normalized_value)
            seen.add(token)
    if len(normalized) < 2:
        raise HTTPException(400, "至少需要 2 个扫描值")
    return normalized


def _scan_value_label(value: Any) -> str:
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    return str(value)


def _build_run_params(base: BacktestParams, overrides: dict[str, Any]) -> BacktestParams:
    run_params = base.model_dump()
    run_params.update(overrides)
    try:
        return BacktestParams(**run_params)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(400, f"参数校验失败: {exc}") from exc


def _execute_backtest(
    params: BacktestParams,
    market: dict,
    *,
    backtest_start: str,
    backtest_end: str | None = None,
) -> dict:
    return backtest_sector_rotation(
        market["etf_data"],
        market["benchmark"],
        top_n=params.top_n,
        rebal_days=params.rebal_days,
        min_hold=params.min_hold,
        score_mode=params.score_mode,
        initial_capital=params.initial_capital,
        max_offensive=params.max_offensive,
        weight_scheme=params.weight_scheme,
        ma200_risk=params.ma200_risk,
        spike_filter=params.spike_filter,
        consec_down_exit=params.consec_down_exit,
        exclude_sectors=set(params.exclude_sectors) if params.exclude_sectors else None,
        rank_offset=params.rank_offset,
        backtest_start=backtest_start,
        backtest_end=backtest_end,
        context=market["context"],
    )


def _metric_value(metrics: dict, key: str, default: float) -> float:
    value = metrics.get(key)
    if value is None:
        return default
    return float(value)


def _objective_tuple(metrics: dict, objective: str) -> tuple[float, float, float]:
    sharpe = _metric_value(metrics, "sharpe", -999.0)
    annual_return = _metric_value(metrics, "annual_return", -999.0)
    mdd = _metric_value(metrics, "max_drawdown", 999.0)
    if objective == "annual_return":
        return (annual_return, -mdd, sharpe)
    if objective == "max_drawdown":
        return (-mdd, sharpe, annual_return)
    return (sharpe, -mdd, annual_return)


def _stitch_equity_curves(results: list[dict]) -> list[dict]:
    stitched: list[dict] = []
    nav_multiplier = 1.0
    for result in results:
        eq = result.get("equity_curve", []) or []
        if not eq:
            continue
        base_nav = float(eq[0]["nav"]) if float(eq[0]["nav"]) > 0 else 1.0
        for point in eq:
            nav = nav_multiplier * (float(point["nav"]) / base_nav)
            entry = {"date": point["date"], "nav": round(nav, 6)}
            if stitched and stitched[-1]["date"] == entry["date"]:
                stitched[-1] = entry
            else:
                stitched.append(entry)
        nav_multiplier = stitched[-1]["nav"]
    return stitched


def _compare_metric_deltas(selected_metrics: dict, baseline_metrics: dict) -> dict:
    keys = [
        "annual_return",
        "total_return",
        "max_drawdown",
        "sharpe",
        "up_day_ratio",
        "trade_win_rate",
        "round_trip_count",
        "avg_hold_days",
        "avg_win_pnl",
        "avg_loss_pnl",
        "avg_round_trip_return",
        "profit_factor",
    ]
    diff = {}
    for key in keys:
        selected = selected_metrics.get(key)
        baseline = baseline_metrics.get(key)
        if selected is None or baseline is None:
            diff[key] = None
        else:
            diff[key] = round(float(selected) - float(baseline), 6)
    return diff


def _start_for_walkforward_window(
    params: BacktestParams,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    scan_param: str,
) -> str:
    if scan_param != "backtest_years":
        return window_start.strftime("%Y-%m-%d")
    candidate_years = max(int(params.backtest_years), 1)
    candidate_start = pd.Timestamp(f"{window_end.year - candidate_years + 1}-01-01")
    return max(window_start, candidate_start).strftime("%Y-%m-%d")


def _format_saved_scan_name(param_name: str, values: list[Any], valid_results: list[dict]) -> str:
    scan_label = SCAN_PARAM_SPECS.get(param_name, {}).get("label", param_name)
    values_str = ", ".join(_scan_value_label(v) for v in values)
    if not valid_results:
        return f"{scan_label}=[{values_str}]"
    best = max(valid_results, key=lambda row: row["metrics"].get("annual_return") or -999)
    best_value = _scan_value_label(best["value"])
    best_cagr = best["metrics"].get("annual_return")
    cagr_pct = float(best_cagr) * 100 if best_cagr is not None else 0
    return f"{scan_label}=[{values_str}] ⭐{best_value} CAGR={cagr_pct:.1f}%"


@router.post("/run")
def run_backtest(params: BacktestParams):
    """运行回测。"""
    market = _load_market_context(params.backtest_years)
    result = _execute_backtest(
        params,
        market,
        backtest_start=_current_start_date(params.backtest_years),
    )
    if "error" in result:
        raise HTTPException(400, result["error"])

    payload = _prepare_api_result(result, params.initial_capital)
    if params.name:
        name = params.name
    else:
        parts = [
            f"{params.backtest_years}yr",
            f"Top{params.top_n}",
            f"R{params.rebal_days}",
            f"H{params.min_hold}",
            params.score_mode,
        ]
        if params.ma200_risk:
            parts.append("MA200")
        if params.spike_filter:
            parts.append("Spike")
        if params.consec_down_exit > 0:
            parts.append(f"CD{params.consec_down_exit}")
        if params.max_offensive is not None and params.max_offensive != 4:
            parts.append(f"Off{params.max_offensive}")
        if params.weight_scheme != "equal":
            parts.append(params.weight_scheme)
        if params.rank_offset > 0:
            parts.append(f"+{params.rank_offset}")
        name = " ".join(parts)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO backtest_runs (name, params_json, metrics_json, equity_curve_json, trades_json, yearly_json, monthly_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                json.dumps(params.model_dump(), ensure_ascii=False),
                json.dumps(payload["metrics"], ensure_ascii=False),
                json.dumps(payload["equity_curve"], ensure_ascii=False),
                json.dumps(payload.get("trades"), ensure_ascii=False),
                json.dumps(payload.get("yearly"), ensure_ascii=False),
                json.dumps(payload.get("monthly"), ensure_ascii=False),
            ),
        )
        backtest_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    payload["id"] = backtest_id
    payload["name"] = name
    return payload


@router.post("/walk-forward")
def walk_forward_backtest(params: BacktestWalkForwardParams):
    """滚动训练窗选参，再展示测试窗表现。"""
    scan_values = _normalize_scan_values(params.scan_param, params.scan_values, limit=10)
    base = params.base_params
    if base.backtest_years <= params.train_years:
        raise HTTPException(400, "回测总年数必须大于训练窗年数")

    market = _load_market_context(base.backtest_years)
    end_dt = pd.Timestamp(_current_end_date())
    overall_start = pd.Timestamp(_current_start_date(base.backtest_years))
    test_start = pd.Timestamp(f"{overall_start.year + params.train_years}-01-01")
    if test_start > end_dt:
        raise HTTPException(400, "当前回测窗口不足以构造 Walk-forward 测试窗")

    windows = []
    test_results = []
    chosen_counter = Counter()

    while test_start <= end_dt:
        train_start = max(overall_start, test_start - pd.DateOffset(years=params.train_years))
        train_end = test_start - pd.Timedelta(days=1)
        test_end = min(test_start + pd.DateOffset(years=params.test_years) - pd.Timedelta(days=1), end_dt)

        train_candidates = []
        for value in scan_values:
            candidate = _build_run_params(base, {params.scan_param: value})
            train_result = _execute_backtest(
                candidate,
                market,
                backtest_start=_start_for_walkforward_window(candidate, train_start, train_end, params.scan_param),
                backtest_end=train_end.strftime("%Y-%m-%d"),
            )
            if "error" not in train_result:
                train_candidates.append((value, candidate, train_result))

        if not train_candidates:
            break

        best_value, best_params, best_train_result = max(
            train_candidates,
            key=lambda item: _objective_tuple(item[2]["metrics"], params.objective),
        )
        test_result = _execute_backtest(
            best_params,
            market,
            backtest_start=test_start.strftime("%Y-%m-%d"),
            backtest_end=test_end.strftime("%Y-%m-%d"),
        )
        if "error" in test_result:
            test_start = test_start + pd.DateOffset(years=params.test_years)
            continue

        chosen_counter[_scan_value_label(best_value)] += 1
        test_results.append(test_result)
        windows.append({
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
            "best_value": best_value,
            "train_metrics": _sanitize_metrics(best_train_result.get("metrics")),
            "test_metrics": _sanitize_metrics(test_result.get("metrics")),
        })
        test_start = test_start + pd.DateOffset(years=params.test_years)

    if not windows:
        raise HTTPException(400, "Walk-forward 未生成有效测试窗")

    stitched_curve = _stitch_equity_curves(test_results)
    all_test_trades = [trade for result in test_results for trade in result.get("trades", [])]
    all_test_round_trips = [trade for result in test_results for trade in result.get("round_trips", [])]
    stitched_result = summarize_backtest_result(
        stitched_curve,
        all_test_trades,
        all_test_round_trips,
        market["benchmark"],
    )
    if "error" in stitched_result:
        raise HTTPException(400, stitched_result["error"])

    oos_payload = _prepare_api_result(stitched_result, base.initial_capital, include_trades=False)
    return {
        "scan_param": params.scan_param,
        "scan_values": scan_values,
        "objective": params.objective,
        "window_mode": params.window_mode,
        "train_years": params.train_years,
        "test_years": params.test_years,
        "base_params": base.model_dump(),
        "windows": windows,
        "oos_metrics": oos_payload["metrics"],
        "oos_trade_stats": oos_payload["trade_stats"],
        "oos_yearly": stitched_result.get("yearly", {}),
        "oos_equity_curve": oos_payload["equity_curve"],
        "chosen_values_summary": [
            {"value": value, "count": count}
            for value, count in chosen_counter.most_common()
        ],
    }


@router.post("/drilldown")
def drilldown_backtest(params: BacktestDrilldownParams):
    """对比热力图单元参数组与基线参数。"""
    overrides = {}
    for key, value in params.overrides.items():
        if key not in SCANNABLE_PARAMS:
            raise HTTPException(400, f"不支持覆盖参数: {key}")
        overrides[key] = _coerce_scan_value(key, value)

    base = params.base_params
    selected = _build_run_params(base, overrides)
    max_years = max(base.backtest_years, selected.backtest_years)
    market = _load_market_context(max_years)

    baseline_result = _execute_backtest(
        base,
        market,
        backtest_start=_current_start_date(base.backtest_years),
    )
    selected_result = _execute_backtest(
        selected,
        market,
        backtest_start=_current_start_date(selected.backtest_years),
    )
    if "error" in baseline_result:
        raise HTTPException(400, baseline_result["error"])
    if "error" in selected_result:
        raise HTTPException(400, selected_result["error"])

    baseline_payload = _prepare_api_result(baseline_result, base.initial_capital)
    selected_payload = _prepare_api_result(selected_result, selected.initial_capital)
    return {
        "base_params": base.model_dump(),
        "overrides": overrides,
        "baseline": baseline_payload,
        "selected": selected_payload,
        "diff": _compare_metric_deltas(selected_payload["metrics"], baseline_payload["metrics"]),
    }


@router.get("/list")
def list_backtests():
    """历史回测列表。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, params_json, metrics_json, created_at "
            "FROM backtest_runs ORDER BY id DESC LIMIT 50"
        ).fetchall()

    result = []
    for row in rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        result.append({
            "id": row["id"],
            "name": row["name"],
            "metrics": metrics,
            "trade_stats": _extract_trade_stats(metrics),
            "created_at": row["created_at"],
        })
    return result


@router.get("/{backtest_id}")
def get_backtest(
    backtest_id: int,
    fields: str = Query(default=None, description="逗号分隔: metrics,yearly,monthly,equity_curve,trades"),
):
    """获取完整回测结果。"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (backtest_id,)).fetchone()
    if not row:
        raise HTTPException(404, "回测不存在")

    result = {
        "id": row["id"],
        "name": row["name"],
        "params": json.loads(row["params_json"]) if row["params_json"] else {},
        "created_at": row["created_at"],
    }
    requested = set(fields.split(",")) if fields else {"metrics", "yearly", "monthly", "equity_curve", "trades"}

    metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
    if "metrics" in requested:
        result["metrics"] = metrics
        result["trade_stats"] = _extract_trade_stats(metrics)
    if "yearly" in requested:
        result["yearly"] = json.loads(row["yearly_json"]) if row["yearly_json"] else {}
    if "monthly" in requested:
        result["monthly"] = json.loads(row["monthly_json"]) if row["monthly_json"] else {}
    if "equity_curve" in requested:
        eq_raw = json.loads(row["equity_curve_json"]) if row["equity_curve_json"] else []
        if eq_raw and "nav" in eq_raw[0] and "value" not in eq_raw[0]:
            capital = result["params"].get("initial_capital", 100000)
            eq_raw = _map_equity_curve(eq_raw, capital)
        result["equity_curve"] = eq_raw
    if "trades" in requested:
        result["trades"] = json.loads(row["trades_json"]) if row["trades_json"] else []
    return result


@router.delete("/{backtest_id}")
def delete_backtest(backtest_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM backtest_runs WHERE id=?", (backtest_id,))
    return {"ok": True}


@router.post("/scan")
def scan_backtest(params: BacktestScanParams):
    """参数扫描。"""
    scan_values = _normalize_scan_values(params.scan_param, params.scan_values, limit=10)
    base = params.base_params
    max_years = max(int(v) for v in scan_values) if params.scan_param == "backtest_years" else base.backtest_years
    market = _load_market_context(max_years)

    results = []
    for value in scan_values:
        try:
            run_params = _build_run_params(base, {params.scan_param: value})
        except HTTPException as exc:
            results.append({"value": value, "metrics": None, "error": exc.detail})
            continue
        result = _execute_backtest(
            run_params,
            market,
            backtest_start=_current_start_date(run_params.backtest_years),
        )
        if "error" in result:
            results.append({"value": value, "metrics": None, "error": result["error"]})
            continue
        payload = _prepare_api_result(
            result,
            run_params.initial_capital,
            sample_limit=500,
            include_trades=False,
            include_yearly=False,
            include_monthly=False,
        )
        results.append({
            "value": value,
            "metrics": payload["metrics"],
            "trade_stats": payload["trade_stats"],
            "equity_curve": payload["equity_curve"],
        })

    valid_results = [row for row in results if row.get("metrics")]
    response = {
        "scan_param": params.scan_param,
        "scan_type": "1d",
        "scan_values": scan_values,
        "base_params": base.model_dump(),
        "results": results,
    }
    name = _format_saved_scan_name(params.scan_param, scan_values, valid_results)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO scan_runs (name, scan_param, scan_values_json, base_params_json, results_json, scan_type) "
            "VALUES (?, ?, ?, ?, ?, '1d')",
            (
                name,
                params.scan_param,
                json.dumps(scan_values, ensure_ascii=False),
                json.dumps(base.model_dump(), ensure_ascii=False),
                json.dumps(results, ensure_ascii=False),
            ),
        )
        scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    response["id"] = scan_id
    response["name"] = name
    return response


@router.post("/scan2d")
def scan2d_backtest(params: BacktestScan2DParams):
    """二维参数扫描。"""
    if params.x_param == params.y_param:
        raise HTTPException(400, "X轴和Y轴参数不能相同")
    x_values = _normalize_scan_values(params.x_param, params.x_values, limit=8)
    y_values = _normalize_scan_values(params.y_param, params.y_values, limit=8)
    base = params.base_params
    max_years = base.backtest_years
    if params.x_param == "backtest_years":
        max_years = max(max_years, max(int(v) for v in x_values))
    if params.y_param == "backtest_years":
        max_years = max(max_years, max(int(v) for v in y_values))
    market = _load_market_context(max_years)

    matrix = []
    for y_value in y_values:
        row = []
        for x_value in x_values:
            try:
                run_params = _build_run_params(base, {params.x_param: x_value, params.y_param: y_value})
            except HTTPException as exc:
                row.append({"x": x_value, "y": y_value, "metrics": None, "error": exc.detail})
                continue
            result = _execute_backtest(
                run_params,
                market,
                backtest_start=_current_start_date(run_params.backtest_years),
            )
            if "error" in result:
                row.append({"x": x_value, "y": y_value, "metrics": None, "error": result["error"]})
            else:
                row.append({
                    "x": x_value,
                    "y": y_value,
                    "metrics": _sanitize_metrics(result.get("metrics")),
                    "trade_stats": _extract_trade_stats(result.get("metrics")),
                })
        matrix.append(row)

    response = {
        "scan_type": "2d",
        "base_params": base.model_dump(),
        "x_param": params.x_param,
        "y_param": params.y_param,
        "x_values": x_values,
        "y_values": y_values,
        "matrix": matrix,
    }
    x_label = SCAN_PARAM_SPECS.get(params.x_param, {}).get("label", params.x_param)
    y_label = SCAN_PARAM_SPECS.get(params.y_param, {}).get("label", params.y_param)
    x_str = ",".join(_scan_value_label(v) for v in x_values)
    y_str = ",".join(_scan_value_label(v) for v in y_values)
    name = f"2D {x_label}×{y_label} [{x_str}]×[{y_str}]"

    with get_db() as conn:
        conn.execute(
            "INSERT INTO scan_runs (name, scan_param, scan_values_json, base_params_json, results_json, scan_type) "
            "VALUES (?, ?, ?, ?, ?, '2d')",
            (
                name,
                f"{params.x_param}×{params.y_param}",
                json.dumps({"x": x_values, "y": y_values}, ensure_ascii=False),
                json.dumps(base.model_dump(), ensure_ascii=False),
                json.dumps(response, ensure_ascii=False),
            ),
        )
        scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    response["id"] = scan_id
    response["name"] = name
    return response


@router.get("/scan/list")
def list_scans():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, scan_param, scan_values_json, base_params_json, scan_type, created_at "
            "FROM scan_runs ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "scan_param": row["scan_param"],
            "scan_values": json.loads(row["scan_values_json"]) if row["scan_values_json"] else [],
            "scan_type": row["scan_type"] or "1d",
            "base_params": json.loads(row["base_params_json"]) if row["base_params_json"] else {},
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.get("/scan/{scan_id}")
def get_scan(scan_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
    if not row:
        raise HTTPException(404, "扫描记录不存在")

    scan_type = row["scan_type"] or "1d"
    raw = json.loads(row["results_json"]) if row["results_json"] else ([] if scan_type == "1d" else {})
    base_params = json.loads(row["base_params_json"]) if row["base_params_json"] else {}
    if scan_type == "2d" and isinstance(raw, dict):
        return {
            "id": row["id"],
            "name": row["name"],
            "scan_param": row["scan_param"],
            "scan_type": scan_type,
            "base_params": base_params,
            "created_at": row["created_at"],
            "x_param": raw.get("x_param", ""),
            "y_param": raw.get("y_param", ""),
            "x_values": raw.get("x_values", []),
            "y_values": raw.get("y_values", []),
            "matrix": raw.get("matrix", []),
        }
    results = raw.get("results", raw) if isinstance(raw, dict) else raw
    return {
        "id": row["id"],
        "name": row["name"],
        "scan_param": row["scan_param"],
        "scan_type": scan_type,
        "results": results,
        "base_params": base_params,
        "created_at": row["created_at"],
    }


@router.delete("/scan/{scan_id}")
def delete_scan(scan_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM scan_runs WHERE id=?", (scan_id,))
    return {"ok": True}
