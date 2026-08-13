from resolution.keys import MODERATE, STRONG, IdentityKey
from resolution.scoring import (
    AUTO_LINK_THRESHOLD,
    DECISION_LINK,
    DECISION_NEW,
    DECISION_REVIEW,
    decide,
    score_candidate,
)


def key(key_type, value="v", strength=STRONG):
    return IdentityKey(key_type, value, strength)


# --------------------------------------------------------------------------
# the rule that matters most: moderate evidence can never auto-link
# --------------------------------------------------------------------------

def test_no_amount_of_moderate_agreement_reaches_the_auto_link_threshold():
    """Three colleagues share a company, a city and a switchboard number.

    If moderate keys could accumulate past the line, they would become one
    person — which is precisely the blind merge the spec forbids.
    """
    matched = [
        key("name_company", strength=MODERATE),
        key("name_city", strength=MODERATE),
        key("phone", strength=MODERATE),
    ]
    match = score_candidate("person", "e1", matched)
    assert match.confidence < AUTO_LINK_THRESHOLD
    assert decide("person", {"e1": matched}).decision == DECISION_REVIEW


def test_a_single_strong_key_links_on_its_own():
    match = score_candidate("person", "e1", [key("email")])
    assert match.confidence >= AUTO_LINK_THRESHOLD
    assert decide("person", {"e1": [key("email")]}).decision == DECISION_LINK


def test_a_vendors_own_id_is_the_strongest_evidence_there_is():
    match = score_candidate("company", "e1", [key("external_id")])
    assert match.confidence >= 0.98


def test_corroboration_helps_but_only_a_little():
    """A second signal should tip a borderline call, not manufacture certainty."""
    alone = score_candidate("company", "e1", [key("name", strength=MODERATE)])
    with_phone = score_candidate(
        "company", "e1",
        [key("name", strength=MODERATE), key("phone", "p", MODERATE)],
    )
    assert with_phone.confidence > alone.confidence
    assert with_phone.confidence < AUTO_LINK_THRESHOLD


def test_repeating_one_kind_of_evidence_is_not_corroboration():
    """Three matching phone numbers is one kind of evidence, not three."""
    one = score_candidate("company", "e1", [key("phone", "a", MODERATE)])
    three = score_candidate("company", "e1", [
        key("phone", "a", MODERATE), key("phone", "b", MODERATE),
        key("phone", "c", MODERATE),
    ])
    assert one.confidence == three.confidence


def test_the_strongest_key_names_the_method():
    match = score_candidate("company", "e1", [
        key("phone", "p", MODERATE), key("website_domain", "x.com"),
    ])
    assert match.method == "website_domain"


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def test_no_candidates_means_a_new_entity():
    decision = decide("company", {})
    assert decision.decision == DECISION_NEW
    assert decision.match is None


def test_weak_evidence_alone_creates_a_new_entity_rather_than_guessing():
    decision = decide("company", {"e1": [key("phone", "p", MODERATE)]})
    assert decision.decision == DECISION_NEW


def test_two_entities_matching_equally_well_goes_to_review_not_a_coin_flip():
    """A tie usually means those two entities are duplicates of each other."""
    decision = decide("company", {
        "e1": [key("website_domain", "x.com")],
        "e2": [key("website_domain", "x.com")],
    })
    assert decision.decision == DECISION_REVIEW
    assert len(decision.considered) == 2


def test_a_clear_winner_links_even_when_others_matched():
    decision = decide("company", {
        "e1": [key("website_domain", "x.com")],
        "e2": [key("phone", "p", MODERATE)],
    })
    assert decision.decision == DECISION_LINK
    assert decision.match.entity_id == "e1"


def test_every_candidate_considered_is_recorded_not_just_the_winner():
    """A link is only explainable if what it beat is recorded too."""
    decision = decide("company", {
        "e1": [key("website_domain", "x.com")],
        "e2": [key("name", "n", MODERATE)],
    })
    assert {m.entity_id for m in decision.considered} == {"e1", "e2"}
    assert "matched_keys" in decision.match.evidence


def test_evidence_names_the_keys_that_matched():
    decision = decide("company", {"e1": [key("email", "a@x.com")]})
    matched = decision.match.evidence["matched_keys"]
    assert matched == [{"key_type": "email", "key_value": "a@x.com"}]
