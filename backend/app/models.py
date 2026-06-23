# 作者：相空
"""
Pydantic 模型 — API 请求/响应
"""
import math
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

ScanScalar = Union[bool, int, float, str]


def _normalize_scan_scalar(v: ScanScalar) -> ScanScalar:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            raise ValueError("扫描值必须全部为有限数字")
        if float(v).is_integer():
            return int(v)
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            raise ValueError("扫描值不能为空字符串")
        return s
    raise ValueError("扫描值类型不支持")


# ─── Signal ──────────────────────────────────────────────
class SignalParams(BaseModel):
    top_n: int = 5
    score_mode: str = "rank_momentum"
    min_hold: int = 15
    max_offensive: int = 4
    ma200_risk: bool = True
    spike_filter: bool = True
    consec_down: int = 3
    force_refresh: bool = False
    # 板块轮动专用参数
    rps_threshold: float = Field(default=85, ge=50, le=100, description="RPS(20) 入场阈值")
    rps60_threshold: float = Field(default=80, ge=50, le=100, description="RPS(60) 入场阈值")
    rps_exit_threshold: float = Field(default=50, ge=10, le=90, description="RPS 离场阈值")
    vol_ratio_entry: float = Field(default=1.3, ge=0.5, le=5.0, description="量比入场阈值")
    vol_ratio_exit: float = Field(default=0.8, ge=0.1, le=2.0, description="量比离场阈值")
    vol_consec_days: int = Field(default=3, ge=1, le=10, description="量比连续达标天数")
    surge_min_stocks: int = Field(default=3, ge=1, le=20, description="异常涨幅最少股数")


# ─── Holdings ────────────────────────────────────────────
class HoldingCreate(BaseModel):
    sector: str
    code: str
    buy_date: str
    buy_price: float
    shares: int
    notes: Optional[str] = None


class HoldingUpdate(BaseModel):
    buy_price: Optional[float] = None
    shares: Optional[int] = None
    notes: Optional[str] = None


class HoldingClose(BaseModel):
    close_price: float
    close_date: Optional[str] = None  # 默认用今天


# ─── Portfolio ────────────────────────────────────────────
class CashUpdate(BaseModel):
    amount: float
    reason: Optional[str] = None


class CapitalUpdate(BaseModel):
    initial_capital: float


# ─── Backtest ─────────────────────────────────────────────
class BacktestParams(BaseModel):
    name: Optional[str] = None
    backtest_years: int = Field(default=5, ge=1, le=20)
    top_n: int = Field(default=5, ge=1, le=20)
    rebal_days: int = Field(default=1, ge=1, le=60)
    min_hold: int = Field(default=15, ge=0, le=120)
    score_mode: Literal["rank_momentum", "mixed", "pure20", "sector_rotation"] = "rank_momentum"
    initial_capital: float = Field(default=100_000, gt=0)
    max_offensive: int = Field(default=4, ge=1, le=10)
    weight_scheme: Literal["equal", "momentum", "inv_vol", "pyramid", "top_heavy"] = "equal"
    ma200_risk: bool = False
    spike_filter: bool = False
    consec_down_exit: int = Field(default=0, ge=0, le=15)
    exclude_sectors: Optional[List[str]] = None
    rank_offset: int = Field(default=0, ge=0, le=10)


# ─── Backtest Scan ────────────────────────────────────────
class BacktestScanParams(BaseModel):
    base_params: BacktestParams = Field(default_factory=BacktestParams)
    scan_param: str = Field(..., description="要扫描的参数名, 如 top_n / min_hold / rebal_days")
    scan_values: List[ScanScalar] = Field(..., description="扫描值列表, 最多10个")

    @field_validator("scan_values")
    @classmethod
    def validate_scan_values(cls, v: List[ScanScalar]) -> List[ScanScalar]:
        return [_normalize_scan_scalar(x) for x in v]


# ─── Backtest 2D Scan ────────────────────────────────────
class BacktestScan2DParams(BaseModel):
    base_params: BacktestParams = Field(default_factory=BacktestParams)
    x_param: str = Field(..., description="X轴参数名")
    x_values: List[ScanScalar] = Field(..., description="X轴扫描值列表, 最多8个")
    y_param: str = Field(..., description="Y轴参数名")
    y_values: List[ScanScalar] = Field(..., description="Y轴扫描值列表, 最多8个")

    @field_validator("x_values", "y_values")
    @classmethod
    def validate_2d_scan_values(cls, v: List[ScanScalar]) -> List[ScanScalar]:
        return [_normalize_scan_scalar(x) for x in v]


class BacktestWalkForwardParams(BaseModel):
    base_params: BacktestParams = Field(default_factory=BacktestParams)
    scan_param: str = Field(..., description="训练窗内要优化的参数名")
    scan_values: List[ScanScalar] = Field(..., description="候选参数值")
    objective: Literal["sharpe", "annual_return", "max_drawdown"] = "sharpe"
    window_mode: Literal["rolling"] = "rolling"
    train_years: int = Field(default=3, ge=1, le=10)
    test_years: int = Field(default=1, ge=1, le=5)

    @field_validator("scan_values")
    @classmethod
    def validate_walkforward_values(cls, v: List[ScanScalar]) -> List[ScanScalar]:
        return [_normalize_scan_scalar(x) for x in v]


class BacktestDrilldownParams(BaseModel):
    base_params: BacktestParams = Field(default_factory=BacktestParams)
    overrides: Dict[str, ScanScalar] = Field(default_factory=dict)
