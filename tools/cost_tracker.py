from __future__ import annotations
import os
import json
import uuid
import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import yaml

from db.session import SessionLocal
from db.models import Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

class DailyBudgetCapExceeded(Exception):
    """Raised when cumulative daily LLM API spend exceeds configured budget cap."""
    pass

# Pricing in USD per million tokens
MODEL_PRICING = {
    "claude-3-5-sonnet": {"prompt": 3.00, "completion": 15.00},
    "claude-3-haiku": {"prompt": 0.25, "completion": 1.25},
    "claude-3-opus": {"prompt": 15.00, "completion": 75.00},
    "default": {"prompt": 3.00, "completion": 15.00}
}

class CostTracker:
    """Tracks LLM token usage and enforces daily budget cap across agent executions."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (PROJECT_ROOT / "config" / "filters.yaml")
        self.daily_cap = self._load_daily_cap()

    def _load_daily_cap(self) -> float:
        """Load daily budget cap from config/filters.yaml."""
        if not self.config_path.exists():
            return 200.0
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return float(cfg.get("limits", {}).get("daily_budget_dollars", 200.0))
        except Exception:
            return 200.0

    def calculate_cost(self, tokens_in: int, tokens_out: int, model: str = "claude-3-5-sonnet") -> float:
        """Calculate USD cost for token count based on model pricing."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        cost_in = (tokens_in / 1_000_000) * pricing["prompt"]
        cost_out = (tokens_out / 1_000_000) * pricing["completion"]
        return round(cost_in + cost_out, 6)

    def get_daily_spend(self, target_date: Optional[datetime.date] = None) -> float:
        """Compute cumulative spend in USD recorded in database for target date (default: today UTC)."""
        if target_date is None:
            target_date = datetime.datetime.now(datetime.timezone.utc).date()

        start_dt = datetime.datetime.combine(target_date, datetime.time.min)
        end_dt = datetime.datetime.combine(target_date, datetime.time.max)

        total_cost = 0.0
        with SessionLocal() as db:
            events = (
                db.query(Event)
                .filter(
                    Event.type == "llm_usage",
                    Event.ts >= start_dt,
                    Event.ts <= end_dt
                )
                .all()
            )
            for e in events:
                try:
                    payload = json.loads(e.payload_json or "{}")
                    total_cost += float(payload.get("cost_usd", 0.0))
                except Exception:
                    pass

        return round(total_cost, 4)

    def check_budget_or_raise(self, additional_cost: float = 0.0) -> bool:
        """Check if today's spend + additional_cost exceeds daily budget cap."""
        today_spend = self.get_daily_spend()
        if (today_spend + additional_cost) > self.daily_cap:
            raise DailyBudgetCapExceeded(
                f"BUDGET CAP HALTED: Cumulative LLM spend today is ${today_spend:.2f} + projected ${additional_cost:.2f}, "
                f"which exceeds the configured daily budget cap of ${self.daily_cap:.2f} (from config/filters.yaml). "
                f"Operations halted to prevent unauthorized charges."
            )
        return True

    def record_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        model: str = "claude-3-5-sonnet",
        agent_name: str = "agent",
        task_desc: str = "",
        application_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record token usage, compute cost, check budget cap, and log audit event."""
        cost = self.calculate_cost(tokens_in, tokens_out, model)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        # Enforce budget cap
        self.check_budget_or_raise(additional_cost=cost)

        # Log event in database
        with SessionLocal() as db:
            evt_id = f"evt_llm_{uuid.uuid4().hex[:16]}"
            payload = {
                "model": model,
                "agent": agent_name,
                "task": task_desc,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
                "daily_cap_usd": self.daily_cap
            }
            evt = Event(
                id=evt_id,
                application_id=application_id or "global",
                ts=now,
                actor="agent",
                type="llm_usage",
                payload_json=json.dumps(payload)
            )
            db.add(evt)
            db.commit()

        return {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "daily_spend_today": round(self.get_daily_spend(), 4),
            "cap_usd": self.daily_cap
        }
