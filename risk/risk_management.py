"""
risk/risk_management.py
========================
Implements review-framework item #7 (Risk Management), applied at the
position/account level — independent of any single signal's quality.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RiskConfig:
    risk_percent_per_trade: float = 1.0     # user-configurable in the Streamlit settings page
    max_daily_loss_percent: float = 3.0
    max_consecutive_losses: int = 3
    min_rr: float = 1.5
    max_concurrent_trades: int = 1
    max_correlated_concurrent: int = 1       # e.g. don't run BTC + ETH stop-hunt longs simultaneously
    correlated_groups: List[List[str]] = field(default_factory=lambda: [["BTC", "ETH"]])
    account_equity: float = 1000.0
    fee_percent_roundtrip: float = 0.08      # placeholder — MUST be set from the actual exchange fee schedule the user trades on
    slippage_percent: float = 0.05           # placeholder — MUST be calibrated from real fill data, not assumed


@dataclass
class DailyState:
    date: str
    realized_pnl_percent: float = 0.0
    consecutive_losses: int = 0
    open_trades: int = 0


class RiskManager:
    def __init__(self, config: RiskConfig):
        self.config = config

    def position_size(self, entry: float, stop_loss: float) -> float:
        risk_amount = self.config.account_equity * (self.config.risk_percent_per_trade / 100.0)
        per_unit_risk = abs(entry - stop_loss)
        if per_unit_risk <= 0:
            return 0.0
        return risk_amount / per_unit_risk

    def is_trade_allowed(self, state: DailyState, rr_actual: float, symbol: str,
                          currently_open_symbols: List[str]) -> (bool, str):
        if rr_actual < self.config.min_rr:
            return False, f"R:R {rr_actual:.2f} below minimum {self.config.min_rr}"
        if state.consecutive_losses >= self.config.max_consecutive_losses:
            return False, "Max consecutive losses reached for today"
        if state.realized_pnl_percent <= -abs(self.config.max_daily_loss_percent):
            return False, "Max daily loss reached"
        if state.open_trades >= self.config.max_concurrent_trades:
            return False, "Max concurrent trades reached"
        for group in self.config.correlated_groups:
            if symbol in group:
                open_in_group = sum(1 for s in currently_open_symbols if s in group)
                if open_in_group >= self.config.max_correlated_concurrent:
                    return False, f"Max correlated concurrent trades reached for group {group}"
        return True, "OK"

    def net_of_costs(self, gross_pnl_percent: float) -> float:
        """Applies fee + slippage haircut. Both values must be replaced with
        the real numbers for the venue/instrument the user actually trades —
        they are exposed as config, not buried as constants."""
        return gross_pnl_percent - self.config.fee_percent_roundtrip - self.config.slippage_percent
