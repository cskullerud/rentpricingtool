import pytest

from app.schemas import ValuationRequest
from app.services.quality import Funnel
from app.ui import csrf, viewmodels
from app.ui.forms import ValuationForm


def form(**overrides):
    data = {"address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "latitude": "", "longitude": ""}
    data.update(overrides)
    return ValuationForm.from_form(data)


# --- forms: the valid case --------------------------------------------------------------

def test_blank_form_has_no_errors_and_is_not_valid():
    blank = ValuationForm.blank()
    assert blank.errors == {} and not blank.is_valid
    # the text fields are empty; the three selects start on their defaults (1 mile, 90 days, all types)
    assert blank.values == {
        "address": "", "beds": "", "baths": "", "sqft": "", "latitude": "", "longitude": "",
        "search_radius_miles": "1", "lookback_days": "90", "property_type": "all",
    }


def test_valid_form_becomes_the_existing_valuation_request():
    f = form()
    assert f.is_valid and f.errors == {}
    assert f.request == ValuationRequest(address="123 Main St", beds=3, baths=2.0, sqft=1400)
    assert f.request.latitude is None and f.request.longitude is None


def test_valid_form_with_coordinates():
    f = form(latitude="32.7678", longitude="-117.0231")
    assert f.is_valid
    assert (f.request.latitude, f.request.longitude) == (32.7678, -117.0231)


def test_values_are_trimmed():
    assert form(address="  123 Main St  ", beds=" 3 ").values["address"] == "123 Main St"


# --- address ----------------------------------------------------------------------------

@pytest.mark.parametrize("address", ["", "   "])
def test_address_is_required(address):
    f = form(address=address)
    assert "address" in f.errors and not f.is_valid


def test_address_length_limit():
    assert "address" in form(address="x" * 201).errors
    assert form(address="x" * 200).is_valid


# --- bedrooms: >= 0 ---------------------------------------------------------------------

def test_zero_bedrooms_is_a_valid_studio():
    f = form(beds="0")
    assert f.is_valid and f.request.beds == 0


@pytest.mark.parametrize("beds", ["", "-1", "2.5", "abc", "1e2", "+3", "51", "3 beds"])
def test_invalid_bedrooms(beds):
    assert "beds" in form(beds=beds).errors


def test_bedroom_messages():
    assert form(beds="").errors["beds"] == "Enter the number of bedrooms."
    assert "whole number" in form(beds="2.5").errors["beds"]


# --- bathrooms: >= 0 --------------------------------------------------------------------

@pytest.mark.parametrize("baths, expected", [("0", 0.0), ("1", 1.0), ("1.5", 1.5), ("2.25", 2.25)])
def test_valid_bathrooms(baths, expected):
    f = form(baths=baths)
    assert f.is_valid and f.request.baths == expected


@pytest.mark.parametrize("baths", ["", "-1", "abc", "nan", "inf", "1e2", "1,5", "51"])
def test_invalid_bathrooms(baths):
    assert "baths" in form(baths=baths).errors


# --- square feet: > 0 -------------------------------------------------------------------

@pytest.mark.parametrize("sqft", ["0", "-5", "abc", "1.5", "1e3", "100001"])
def test_invalid_square_footage(sqft):
    assert "sqft" in form(sqft=sqft).errors


@pytest.mark.parametrize("sqft", ["", "   "])
def test_blank_square_footage_is_allowed_and_means_no_size_matching(sqft):
    f = form(sqft=sqft)
    assert f.is_valid and f.errors == {} and f.request.sqft is None


def test_square_footage_must_be_above_zero():
    assert form(sqft="0").errors["sqft"] == "The square footage must be at least 1."
    assert form(sqft="1").is_valid


@pytest.mark.parametrize("sqft", ["1400", "1,400", "1 400"])
def test_square_footage_accepts_separators(sqft):
    assert form(sqft=sqft).request.sqft == 1400


# --- coordinates: both or neither ---------------------------------------------------------

def test_both_coordinates_empty_is_fine():
    f = form()
    assert f.is_valid and not f.show_coordinates


@pytest.mark.parametrize("lat, lon, missing", [("32.7", "", "longitude"), ("", "-117.0", "latitude")])
def test_one_coordinate_alone_is_rejected(lat, lon, missing):
    f = form(latitude=lat, longitude=lon)
    assert list(f.errors) == [missing]
    assert "both latitude and longitude" in f.errors[missing]
    assert f.show_coordinates and not f.is_valid


@pytest.mark.parametrize(
    "lat, lon, bad",
    [("91", "0", "latitude"), ("-91", "0", "latitude"), ("0", "181", "longitude"), ("0", "-181", "longitude"),
     ("abc", "0", "latitude"), ("0", "nan", "longitude"), ("0", "1e2", "longitude")],
)
def test_invalid_coordinates(lat, lon, bad):
    assert bad in form(latitude=lat, longitude=lon).errors


def test_coordinate_boundaries_are_valid():
    assert form(latitude="90", longitude="-180").is_valid
    assert form(latitude="-90", longitude="180").is_valid


# --- preserving what was typed ------------------------------------------------------------

def test_every_entered_value_is_kept_when_validation_fails():
    f = form(address="  9 Elm St ", beds="", baths="1.5", sqft="0", latitude="32.7", longitude="")
    assert not f.is_valid
    assert f.values == {
        "address": "9 Elm St", "beds": "", "baths": "1.5", "sqft": "0", "latitude": "32.7", "longitude": "",
        "search_radius_miles": "1", "lookback_days": "90", "property_type": "all",
    }


def test_all_errors_are_reported_at_once():
    f = form(address="", beds="", baths="", sqft="0")
    assert set(f.errors) == {"address", "beds", "baths", "sqft"}


def test_missing_or_non_text_fields_count_as_empty():
    f = ValuationForm.from_form({"address": object(), "beds": None})
    assert f.values["address"] == "" and f.values["beds"] == "" and f.values["sqft"] == ""


def test_explicit_expand_flag_opens_the_coordinates_section():
    f = form()
    f.expand_coordinates = True
    assert f.show_coordinates


# --- csrf ---------------------------------------------------------------------------------

def test_token_verifies_against_its_cookie():
    cookie = csrf.new_cookie_value()
    assert csrf.verify(cookie, csrf.token_for(cookie))


def test_token_is_stable_for_a_cookie_and_differs_between_cookies():
    a, b = csrf.new_cookie_value(), csrf.new_cookie_value()
    assert a != b
    assert csrf.token_for(a) == csrf.token_for(a) != csrf.token_for(b)


@pytest.mark.parametrize("token", [None, "", "wrong", "0" * 64])
def test_wrong_tokens_are_rejected(token):
    assert not csrf.verify(csrf.new_cookie_value(), token)


def test_token_from_another_cookie_is_rejected():
    mine, theirs = csrf.new_cookie_value(), csrf.new_cookie_value()
    assert not csrf.verify(mine, csrf.token_for(theirs))


@pytest.mark.parametrize("cookie", [None, "", "short", "has spaces in it......................", "x" * 101, "bad/chars/here/....................."])
def test_missing_or_malformed_cookies_are_rejected(cookie):
    assert not csrf.is_well_formed(cookie)
    assert not csrf.verify(cookie, "anything")


def test_a_configured_secret_gives_stable_tokens_and_a_changed_secret_invalidates_them(monkeypatch):
    cookie = csrf.new_cookie_value()
    monkeypatch.setenv("UI_SECRET_KEY", "secret-one")
    first = csrf.token_for(cookie)
    assert csrf.token_for(cookie) == first and csrf.verify(cookie, first)
    monkeypatch.setenv("UI_SECRET_KEY", "secret-two")
    assert not csrf.verify(cookie, first)


# --- view models ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [(2512, "$2,512"), (950, "$950"), (12000, "$12,000"), (2511.6, "$2,512")])
def test_money(value, shown):
    assert viewmodels.money(value) == shown


@pytest.mark.parametrize("level, css, label", [
    ("low", "text-bg-warning", "Low confidence"),
    ("medium", "text-bg-info", "Medium confidence"),
    ("high", "text-bg-success", "High confidence"),
])
def test_confidence_style(level, css, label):
    style = viewmodels.confidence_style(level)
    assert (style["css"], style["label"], style["level"]) == (css, label, level)
    assert style["meaning"]


def test_funnel_rows_order_percentages_and_losses():
    rows = viewmodels.funnel_rows(Funnel(26, 24, 18, 16, 16).as_dict())
    assert [r["count"] for r in rows] == [26, 24, 18, 16, 16]
    assert [r["percent"] for r in rows] == [100, 92, 69, 62, 62]
    assert [r["lost"] for r in rows] == [0, 2, 6, 2, 0]
    assert rows[0]["label"] == "Fetched from the data source" and rows[-1]["label"] == "Used for pricing"
    assert "1 mile" in rows[1]["label"]


def test_funnel_rows_with_nothing_fetched():
    rows = viewmodels.funnel_rows(Funnel(0, 0, 0, 0, 0).as_dict())
    assert [r["percent"] for r in rows] == [0] * 5 and [r["lost"] for r in rows] == [0] * 5


@pytest.mark.parametrize(
    "funnel, fragment",
    [
        (Funnel(0, 0, 0, 0, 0), "returned no listings"),
        (Funnel(2, 2, 2, 2, 2), "returned only 2 listings"),
        (Funnel(1, 1, 1, 1, 1), "returned only 1 listing "),
        (Funnel(12, 2, 2, 2, 2), "Only 2 of 12 listings are within 1 mile"),
        (Funnel(12, 8, 1, 1, 1), "Only 1 nearby listing had similar features"),
        (Funnel(12, 8, 4, 2, 2), "Removing unusual rents left only 2 comparables"),
    ],
)
def test_explain_insufficient(funnel, fragment):
    assert fragment in viewmodels.explain_insufficient(funnel.as_dict(), 3)


def test_explanation_names_the_first_stage_that_ran_short():
    assert "within 1 mile" in viewmodels.explain_insufficient(Funnel(10, 2, 1, 1, 1).as_dict(), 3)
