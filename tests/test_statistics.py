import pytest

from app.services.comparables import (
    filter_by_bathrooms,
    filter_by_bedrooms,
    filter_by_distance,
    filter_by_sqft,
)
from app.services.data_sources.mock_source import MockComparableSource
from app.services.statistics import (
    calculate_average,
    calculate_percentiles,
    calculate_std_dev,
    remove_outliers,
)


def test_calculate_percentiles_interpolates():
    result = calculate_percentiles([1, 2, 3, 4, 5])
    assert result == {"p25": 2, "median": 3, "p75": 4}


def test_calculate_percentiles_even_count_uses_midpoint():
    assert calculate_percentiles([10, 20, 30, 40])["median"] == 25


def test_calculate_percentiles_does_not_depend_on_input_order():
    assert calculate_percentiles([5, 1, 4, 2, 3]) == calculate_percentiles([1, 2, 3, 4, 5])


def test_calculate_average():
    assert calculate_average([2000, 2500, 3000]) == 2500


def test_calculate_std_dev_is_sample_deviation():
    assert calculate_std_dev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)
    assert calculate_std_dev([2500]) == 0.0


@pytest.mark.parametrize("fn", [calculate_percentiles, calculate_average, calculate_std_dev])
def test_empty_input_raises(fn):
    with pytest.raises(ValueError):
        fn([])


def test_remove_outliers_drops_900_and_8000():
    # The comparables that match a 3 bed / 2 bath / 1400 sqft subject, as the engine sees them.
    matches = MockComparableSource().get_comparables()
    matches = filter_by_distance(matches)
    matches = filter_by_bedrooms(matches, 3)
    matches = filter_by_bathrooms(matches, 2)
    matches = filter_by_sqft(matches, 1400)
    rents = [c["rent"] for c in matches]
    assert 900 in rents and 8000 in rents  # present before

    cleaned = remove_outliers(rents)

    assert 900 not in cleaned
    assert 8000 not in cleaned
    assert len(cleaned) == len(rents) - 2  # only those two are removed


def test_remove_outliers_keeps_normal_values_and_order():
    rents = [2500, 900, 2400, 2600, 8000, 2550]
    assert remove_outliers(rents) == [2500, 2400, 2600, 2550]


def test_remove_outliers_with_too_few_values_keeps_all():
    assert remove_outliers([900, 8000, 2500]) == [900, 8000, 2500]
