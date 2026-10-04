"""Optional live check of the real Census geocoder. SKIPPED unless explicitly enabled:

    RUN_LIVE_CENSUS_TESTS=1 .venv/bin/python -m pytest tests/test_census_live.py -q

It makes real requests to the free Census service (no key, no cost, no RentCast). Run it once to
confirm that the service needs no key and answers in the documented shape, then record the real
response as the fixture in test_census_geocoder.py. It is never part of the normal run.
"""
import os

import pytest

from app.services.geocoding import AddressNotFoundError, CensusGeocoder, CensusSettings
from app.services.geo import miles_between_points

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CENSUS_TESTS") != "1",
    reason="live Census test; set RUN_LIVE_CENSUS_TESTS=1 to run it (real network requests)",
)

# The address used in the Census geocoder's own documentation.
DOCS_ADDRESS = "4600 Silver Hill Rd, Washington, DC 20233"
DOCS_POINT = (38.846, -76.927)


def test_live_lookup_of_the_documentation_address():
    found = CensusGeocoder(CensusSettings(max_retries=2)).geocode(DOCS_ADDRESS)
    assert miles_between_points(found["latitude"], found["longitude"], *DOCS_POINT) < 0.5


def test_live_unit_suffix_gives_the_same_point():
    geocoder = CensusGeocoder(CensusSettings(max_retries=2))
    plain, with_unit = geocoder.geocode(DOCS_ADDRESS), geocoder.geocode("4600 Silver Hill Rd Apt 4B, Washington, DC 20233")
    assert plain == with_unit


def test_live_unknown_address_is_not_found():
    with pytest.raises(AddressNotFoundError):
        CensusGeocoder(CensusSettings(max_retries=2)).geocode("99999 Nowhere Boulevard, Atlantis, ZZ 00000")
