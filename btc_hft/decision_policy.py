from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class TradeDecision:
    should_trade: bool
    expected_net_bps: float
    confidence: float
    threshold_used: float
    reason: str


@dataclass(frozen=True)
class DecisionInput:
    expected_capture_bps: float
    spread_half_bps: float
    ofi_score: float
    momentum_bps: float
    regime: str
    queue_position: str
    inventory_ratio: float
    estimated_fill_prob: float
    adverse_selection_bps: float
    fee_bps: float
    slippage_bps: float
    uncertainty: float
    toxicity_prob: float


@dataclass(frozen=True)
class DecisionOutcome:
    regime: str
    queue_position: str
    expected_net_bps: float
    realized_net_bps: float
    expected_capture_bps: float
    fill_prob: float
    confidence: float
    fee_bps: float
    slippage_bps: float
    adverse_selection_bps: float


@dataclass
class RegimeCalibration:
    threshold_bps: float
    fill_prob: float
    adverse_selection_bps: float
    slippage_bps: float
    sample_count: int


@dataclass
class CalibrationArtifact:
    version: str
    created_at: str
    regime_params: dict[str, RegimeCalibration]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "regime_params": {
                regime: asdict(params) for regime, params in self.regime_params.items()
            },
        }


class ExpectancyDecisionPolicy:
    DEFAULT_THRESHOLDS = {
        "quiet": 0.12,
        "normal": 0.20,
        "trend": 0.35,
        "high_vol": 0.55,
        "warmup": 0.40,
        "unknown": 0.40,
    }

    DEFAULT_FILL_PROB = {
        "front": 0.70,
        "unknown": 0.45,
        "back": 0.25,
    }

    def __init__(
        self,
        base_threshold_bps: float = 0.2,
        confidence_margin_bps: float = 0.15,
        toxicity_penalty_bps: float = 0.30,
        inventory_penalty_factor: float = 0.25,
        min_confidence: float = 0.20,
        artifact: CalibrationArtifact | None = None,
    ) -> None:
        self.base_threshold_bps = max(0.0, base_threshold_bps)
        self.confidence_margin_bps = max(0.0, confidence_margin_bps)
        self.toxicity_penalty_bps = max(0.0, toxicity_penalty_bps)
        self.inventory_penalty_factor = max(0.0, inventory_penalty_factor)
        self.min_confidence = min(max(min_confidence, 0.0), 1.0)
        self._regime_thresholds = dict(self.DEFAULT_THRESHOLDS)
        self._queue_fill_prob = dict(self.DEFAULT_FILL_PROB)
        self._regime_adverse = {key: 0.20 for key in self.DEFAULT_THRESHOLDS}
        self._regime_slippage = {key: 0.10 for key in self.DEFAULT_THRESHOLDS}
        self._artifact_version = "bootstrap"

        if artifact is not None:
            self.apply_artifact(artifact)

    def _confidence(self, inp: DecisionInput) -> float:
        ofi_conf = min(abs(inp.ofi_score), 1.0) * 0.35
        momentum_conf = min(abs(inp.momentum_bps) / 10.0, 1.0) * 0.25
        queue_conf = 0.20 if inp.queue_position == "front" else 0.10 if inp.queue_position == "unknown" else 0.05
        vol_penalty = min(max(inp.uncertainty, 0.0), 1.0) * 0.30
        base = 0.25 + ofi_conf + momentum_conf + queue_conf - vol_penalty
        return min(max(base, 0.0), 1.0)

    def estimate_fill_probability(self, regime: str, queue_position: str, observed_fill_rate: float | None = None) -> float:
        queue_base = self._queue_fill_prob.get(queue_position, self._queue_fill_prob["unknown"])
        regime_adj = 0.0
        if regime in {"quiet", "normal"}:
            regime_adj = 0.05
        elif regime in {"trend", "high_vol"}:
            regime_adj = -0.08
        estimate = queue_base + regime_adj
        if observed_fill_rate is not None:
            estimate = 0.7 * estimate + 0.3 * max(min(observed_fill_rate, 1.0), 0.0)
        return min(max(estimate, 0.01), 0.99)

    def evaluate(self, inp: DecisionInput) -> TradeDecision:
        fill_prob = min(max(inp.estimated_fill_prob, 0.01), 0.99)
        confidence = self._confidence(inp)
        if confidence < self.min_confidence:
            return TradeDecision(
                should_trade=False,
                expected_net_bps=-1e-9,
                confidence=confidence,
                threshold_used=self._threshold_for(inp.regime, confidence),
                reason="low_signal_confidence",
            )

        inventory_penalty = abs(inp.inventory_ratio) * self.inventory_penalty_factor
        toxicity_penalty = inp.toxicity_prob * self.toxicity_penalty_bps
        post_cost = (
            fill_prob * inp.expected_capture_bps
            - inp.fee_bps
            - inp.slippage_bps
            - inp.adverse_selection_bps
            - inventory_penalty
            - toxicity_penalty
        )

        threshold = self._threshold_for(inp.regime, confidence)
        if post_cost > threshold:
            return TradeDecision(True, post_cost, confidence, threshold, "post_cost_expectancy_ok")

        reason = "post_cost_expectancy_below_threshold"
        if inp.regime in {"trend", "high_vol"} and inp.toxicity_prob >= 0.6:
            reason = "toxic_or_high_vol_block"
        return TradeDecision(False, post_cost, confidence, threshold, reason)

    def _threshold_for(self, regime: str, confidence: float) -> float:
        regime_floor = self._regime_thresholds.get(regime, self.base_threshold_bps)
        confidence_penalty = (1.0 - min(max(confidence, 0.0), 1.0)) * self.confidence_margin_bps
        return max(self.base_threshold_bps, regime_floor + confidence_penalty)

    def apply_artifact(self, artifact: CalibrationArtifact) -> None:
        self._artifact_version = artifact.version
        for regime, params in artifact.regime_params.items():
            self._regime_thresholds[regime] = max(0.01, params.threshold_bps)
            self._regime_adverse[regime] = max(0.0, params.adverse_selection_bps)
            self._regime_slippage[regime] = max(0.0, params.slippage_bps)
            if params.sample_count >= 5:
                baseline = self._queue_fill_prob.get("unknown", 0.45)
                self._queue_fill_prob["unknown"] = 0.8 * baseline + 0.2 * max(min(params.fill_prob, 0.95), 0.05)

    def calibration_state(self) -> dict:
        return {
            "artifact_version": self._artifact_version,
            "base_threshold_bps": self.base_threshold_bps,
            "confidence_margin_bps": self.confidence_margin_bps,
            "regime_thresholds": {k: round(v, 4) for k, v in self._regime_thresholds.items()},
            "regime_adverse_bps": {k: round(v, 4) for k, v in self._regime_adverse.items()},
            "regime_slippage_bps": {k: round(v, 4) for k, v in self._regime_slippage.items()},
        }


def calibrate_policy_from_outcomes(
    outcomes: Iterable[DecisionOutcome],
    output_dir: Path | None = None,
    min_samples_per_regime: int = 10,
) -> CalibrationArtifact:
    rows = list(outcomes)
    regime_groups: dict[str, list[DecisionOutcome]] = {}
    for row in rows:
        regime_groups.setdefault(row.regime, []).append(row)

    params: dict[str, RegimeCalibration] = {}
    for regime, samples in regime_groups.items():
        if len(samples) < min_samples_per_regime:
            continue

        half = max(5, len(samples) // 2)
        train = samples[:half]
        valid = samples[half:]

        train_fill = mean(s.fill_prob for s in train)
        train_slippage = mean(s.slippage_bps for s in train)
        train_adverse = mean(s.adverse_selection_bps for s in train)

        expected_valid = [s.expected_net_bps for s in valid]
        realized_valid = [s.realized_net_bps for s in valid]
        if not expected_valid or not realized_valid:
            continue

        lower = min(expected_valid)
        upper = max(expected_valid)
        if abs(upper - lower) < 1e-9:
            threshold = max(lower, 0.05)
        else:
            candidates = [lower + (upper - lower) * step / 8.0 for step in range(9)]
            best_score = -1e18
            best_threshold = candidates[0]
            for threshold_candidate in candidates:
                selected = [realized_valid[i] for i, e in enumerate(expected_valid) if e > threshold_candidate]
                if len(selected) < max(3, int(0.1 * len(valid))):
                    continue
                utility = mean(selected) - 0.15 * math.sqrt(abs(min(selected)))
                if utility > best_score:
                    best_score = utility
                    best_threshold = threshold_candidate
            threshold = max(best_threshold, 0.01)

        params[regime] = RegimeCalibration(
            threshold_bps=round(float(threshold), 6),
            fill_prob=round(float(train_fill), 6),
            adverse_selection_bps=round(float(train_adverse), 6),
            slippage_bps=round(float(train_slippage), 6),
            sample_count=len(samples),
        )

    version = datetime.now(timezone.utc).strftime("expectancy_%Y%m%d_%H%M%S")
    artifact = CalibrationArtifact(
        version=version,
        created_at=datetime.now(timezone.utc).isoformat(),
        regime_params=params,
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{version}.json"
        target.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")

    return artifact
