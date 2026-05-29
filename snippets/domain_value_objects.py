"""Domain value objects — analysis bounded context.

Value objects are immutable and identified by their values, not by an id.
They live in the domain layer and have zero infrastructure dependencies.

ConfidenceScore: wraps a 0–100 numeric score with a derived label and justification.
ConfidenceFactors: input factors for the scoring engine, each normalized to [0.0, 1.0].
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oracle.domain.analysis.enums import ConfidenceLabel


class ValueObject(BaseModel):
    model_config = ConfigDict(frozen=True)


class ConfidenceScore(ValueObject):
    """Score 0–100 with a derived qualitative label and human-readable justification.

    Constructed via the factory method rather than directly, so the label is
    always derived from the score rather than passed independently.
    """

    score: float = Field(ge=0.0, le=100.0)
    label: ConfidenceLabel
    justification: str

    @field_validator("score")
    @classmethod
    def round_score(cls, v: float) -> float:
        return round(v, 1)

    @classmethod
    def from_value(cls, score: float, justification: str) -> "ConfidenceScore":
        return cls(
            score=round(score, 1),
            label=cls._label_for(score),
            justification=justification,
        )

    @staticmethod
    def _label_for(score: float) -> ConfidenceLabel:
        if score >= 85:
            return ConfidenceLabel.MUITO_ALTA
        if score >= 70:
            return ConfidenceLabel.ALTA
        if score >= 50:
            return ConfidenceLabel.MODERADA
        if score >= 30:
            return ConfidenceLabel.BAIXA
        return ConfidenceLabel.MUITO_BAIXA

    @property
    def is_high_conviction(self) -> bool:
        return self.label in (ConfidenceLabel.ALTA, ConfidenceLabel.MUITO_ALTA)


class ConfidenceFactors(ValueObject):
    """Input factors for the confidence scoring engine.

    Each factor is a normalized float [0.0, 1.0]. The scoring engine applies
    weights to these factors and produces a ConfidenceScore. Immutable by design —
    factors represent a snapshot of market conditions at analysis time.
    """

    trend_alignment: float = Field(ge=0.0, le=1.0, description="Multi-timeframe trend agreement")
    setup_quality: float = Field(ge=0.0, le=1.0, description="Pattern clarity and definition")
    historical_match: float = Field(ge=0.0, le=1.0, description="Similar past setups success rate")
    macro_context: float = Field(ge=0.0, le=1.0, description="News, calendar, macro favorability")
    market_quality: float = Field(ge=0.0, le=1.0, description="Volume, spread, liquidity score")
    emotional_state: float = Field(ge=0.0, le=1.0, description="Trader fitness (1.0 = calm/neutral)")

    @property
    def weighted_average(self) -> float:
        weights = {
            "trend_alignment": 0.25,
            "setup_quality": 0.25,
            "historical_match": 0.20,
            "macro_context": 0.15,
            "market_quality": 0.10,
            "emotional_state": 0.05,
        }
        total = sum(getattr(self, k) * w for k, w in weights.items())
        return round(total * 100, 1)
