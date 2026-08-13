import pytest
from validation.rules import AttributeContext


@pytest.fixture
def ctx():
    """Build an AttributeContext without repeating five keyword arguments."""
    def _ctx(canonical_field, value_type, entity_type="person", raw=None, method=None):
        return AttributeContext(
            canonical_field=canonical_field,
            entity_type=entity_type,
            value_type=value_type,
            raw_value=raw if raw is not None else "",
            normalization_method=method or value_type,
        )
    return _ctx


@pytest.fixture
def outcome_of():
    """Outcome of one rule by id, or None when the rule did not apply."""
    def _outcome(judgements, rule_id):
        for judgement in judgements:
            if judgement.rule_id == rule_id:
                return judgement.outcome
        return None
    return _outcome


@pytest.fixture
def judgement_for():
    def _for(judgements, rule_id):
        return next(j for j in judgements if j.rule_id == rule_id)
    return _for
