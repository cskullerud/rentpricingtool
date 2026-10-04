import pytest

from app.schemas import ValuationRequest
from app.services.data_sources.mock_source import MockComparableSource
from app.services.valuation_engine import NoComparablesError, run_valuation

SUBJECT = ValuationRequest(address="123 Main St", beds=3, baths=2, sqft=1400)
SOURCE = MockComparableSource()


def test_recommended_rent_equals_median():
    result = run_valuation(SUBJECT, SOURCE)
    assert result["recommended_rent"] == result["median"]


def test_has_comparables():
    assert run_valuation(SUBJECT, SOURCE)["comparable_count"] > 0


def test_result_shape_and_ordering():
    result = run_valuation(SUBJECT, SOURCE)
    assert set(result) == {"comparable_count", "p25", "median", "p75", "average", "recommended_rent"}
    assert result["p25"] <= result["median"] <= result["p75"]


def test_outliers_do_not_skew_the_result():
    result = run_valuation(SUBJECT, SOURCE)
    # 900 and 8000 match the filters but must be dropped, leaving 16 of the 18 matches.
    assert result["comparable_count"] == 16
    assert 2200 <= result["average"] <= 2800


def test_is_deterministic():
    assert run_valuation(SUBJECT, SOURCE) == run_valuation(SUBJECT, SOURCE)


def test_different_subjects_give_different_results():
    small = ValuationRequest(address="1 Small St", beds=2, baths=1, sqft=1150)
    assert run_valuation(small, SOURCE) != run_valuation(SUBJECT, SOURCE)


def test_no_comparables_raises():
    huge = ValuationRequest(address="1 Castle Rd", beds=10, baths=8, sqft=9000)
    with pytest.raises(NoComparablesError):
        run_valuation(huge, SOURCE)
