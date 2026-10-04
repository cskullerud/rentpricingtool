import pytest

from app.services.geocoding.normalize import cache_key, clean_address, strip_unit


@pytest.mark.parametrize(
    "address, expected",
    [
        # Apt
        ("123 Main St Apt 4B, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("123 Main St, Apt 4B, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("123 Main St Apt. 4, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Apt #4, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Apartment 12, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St APT 4B, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St apt 4b, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Apt A, San Diego, CA", "123 Main St, San Diego, CA"),
        # Unit
        ("123 Main St, Unit B, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Unit 3-A, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St UNIT 12, San Diego, CA", "123 Main St, San Diego, CA"),
        # Suite
        ("123 Main St Suite 100, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Ste 5, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Ste. 5, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St Suite 100 San Diego CA", "123 Main St San Diego CA"),
        # #number
        ("123 Main St #4, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St # 4, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St, #12B, San Diego, CA", "123 Main St, San Diego, CA"),
        ("123 Main St #4", "123 Main St"),
        # several designators, and one at the start
        ("123 Main St Apt 4 Unit B, San Diego, CA", "123 Main St, San Diego, CA"),
        ("Apt 4 123 Main St", "123 Main St"),
        # nothing to strip
        ("123 Main St", "123 Main St"),
        ("123 Main St, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
    ],
)
def test_units_are_stripped(address, expected):
    assert strip_unit(address) == expected


@pytest.mark.parametrize(
    "address",
    [
        "123 Unit St, San Diego, CA",  # a street called Unit St
        "45 Suite Road, San Diego, CA",  # a plain word after the designator
        "9 Apt Way, San Diego, CA",
        "123 Main St Apt, San Diego, CA",  # designator with nothing after it
        "123 Main St #, San Diego, CA",
        "100 Apartment Ave, San Diego, CA",
        "12 Unity Blvd, San Diego, CA",  # starts like "Unit" but is not a designator
        "7 Sterling Dr, San Diego, CA",  # starts like "Ste"
    ],
)
def test_street_names_that_look_like_units_are_kept(address):
    assert strip_unit(address) == address


def test_stripping_is_idempotent():
    once = strip_unit("123 Main St Apt 4B, San Diego, CA 92101")
    assert strip_unit(once) == once


def test_the_address_text_is_otherwise_untouched():
    assert strip_unit("456 Oak Ave Unit 2, La Mesa, California 91942") == "456 Oak Ave, La Mesa, California 91942"


@pytest.mark.parametrize("address", ["", "   ", None])
def test_empty_input(address):
    assert strip_unit(address) == "" and clean_address(address) == "" and cache_key(address) == ""


def test_an_address_that_is_only_a_unit_becomes_empty():
    assert strip_unit("Apt 4B") == "" and strip_unit("#12") == ""


# --- clean_address -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  123   Main St ,  San Diego ,CA  ", "123 Main St, San Diego, CA"),
        ("123 Main St,\tSan Diego,\nCA", "123 Main St, San Diego, CA"),
        ("123 Main St\x00, San Diego\x07, CA", "123 Main St, San Diego, CA"),
        (",123 Main St,,", "123 Main St"),
    ],
)
def test_clean_address(raw, expected):
    assert clean_address(raw) == expected


def test_clean_address_keeps_case_and_punctuation():
    assert clean_address("123 N. Main St., Apt 4") == "123 N. Main St., Apt 4"


# --- cache_key ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "variant",
    [
        "123 Main St, San Diego, CA 92101",
        "123 MAIN ST, SAN DIEGO, CA 92101",
        "123 Main St.,  San Diego , CA   92101",
        "123 Main St Apt 4B, San Diego, CA 92101",
        "123 Main St #7, San Diego, CA 92101",
        "123 Main St, San Diego, California 92101",
        "  123 main st, san diego, ca 92101  ",
    ],
)
def test_spellings_of_one_address_share_a_key(variant):
    assert cache_key(variant) == "123 main st, san diego, ca 92101"


@pytest.mark.parametrize(
    "other",
    ["124 Main St, San Diego, CA 92101", "123 Main St, San Diego, CA 92102", "123 Main St, La Mesa, CA 92101", "123 Main Ave, San Diego, CA 92101"],
)
def test_different_addresses_get_different_keys(other):
    assert cache_key(other) != cache_key("123 Main St, San Diego, CA 92101")


def test_a_street_called_california_is_not_rewritten():
    assert cache_key("5 California St, San Francisco, CA") == "5 california st, san francisco, ca"
