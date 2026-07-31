"""Pure-function tests for tier-transition validation."""

from __future__ import annotations

import pytest
from beacon_registry.errors import InvalidTierTransitionError
from beacon_registry.provenance import validate_transition
from beacon_registry.types import EvalItemTier


class TestAllowedTransitions:
    def test_initial_creation_to_model_proposed(self) -> None:
        validate_transition(prior=None, new=EvalItemTier.MODEL_PROPOSED)

    def test_model_proposed_to_execution_confirmed(self) -> None:
        validate_transition(
            prior=EvalItemTier.MODEL_PROPOSED,
            new=EvalItemTier.EXECUTION_CONFIRMED,
        )

    def test_execution_confirmed_to_human_verified(self) -> None:
        validate_transition(
            prior=EvalItemTier.EXECUTION_CONFIRMED,
            new=EvalItemTier.HUMAN_VERIFIED,
        )


class TestRejectedReverseTransitions:
    def test_human_verified_to_execution_confirmed_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(
                prior=EvalItemTier.HUMAN_VERIFIED,
                new=EvalItemTier.EXECUTION_CONFIRMED,
            )

    def test_human_verified_to_model_proposed_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(
                prior=EvalItemTier.HUMAN_VERIFIED,
                new=EvalItemTier.MODEL_PROPOSED,
            )

    def test_execution_confirmed_to_model_proposed_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(
                prior=EvalItemTier.EXECUTION_CONFIRMED,
                new=EvalItemTier.MODEL_PROPOSED,
            )


class TestRejectedSkipForward:
    def test_model_proposed_to_human_verified_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(
                prior=EvalItemTier.MODEL_PROPOSED,
                new=EvalItemTier.HUMAN_VERIFIED,
            )

    def test_initial_to_execution_confirmed_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(prior=None, new=EvalItemTier.EXECUTION_CONFIRMED)

    def test_initial_to_human_verified_rejected(self) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(prior=None, new=EvalItemTier.HUMAN_VERIFIED)


class TestRejectedSameTier:
    @pytest.mark.parametrize("tier", list(EvalItemTier))
    def test_same_tier_rejected(self, tier: EvalItemTier) -> None:
        with pytest.raises(InvalidTierTransitionError):
            validate_transition(prior=tier, new=tier)


class TestErrorMessageQuality:
    def test_error_includes_both_tiers(self) -> None:
        with pytest.raises(InvalidTierTransitionError) as exc_info:
            validate_transition(
                prior=EvalItemTier.HUMAN_VERIFIED,
                new=EvalItemTier.MODEL_PROPOSED,
            )

        message = str(exc_info.value)
        assert "human_verified" in message
        assert "model_proposed" in message
