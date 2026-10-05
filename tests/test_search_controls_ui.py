"""The search controls and the Search criteria summary on the web form, and how results show them.
Mock sources only; nothing here can reach the network or RentCast."""
import re
import urllib.request
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.services import comparables as comps
from app.services.data_sources import ComparableDataSource
from app.services.search_options import LOOKBACK_OPTIONS_DAYS, PROPERTY_TYPES, RADIUS_OPTIONS_MILES

MILES_PER_DEGREE = 69.05
FORM = {"address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "latitude": str(LAT), "longitude": str(LON)}
SUMMARY_LABELS = [
    "Address", "Search radius", "Distance units", "Lookback window", "Building type", "Minimum comparables",
    "Bedroom filter", "Bathroom filter", "Size matching", "Outlier handling", "Listings considered",
    "Recommended rent", "Confidence", "Address lookup",
]


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("a UI test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    for name in ("DATA_PROVIDER", "RENTCAST_API_KEY", "GEOCODER"):
        monkeypatch.delenv(name, raising=False)
    yield
    app.dependency_overrides.pop(get_comparable_source, None)


@pytest.fixture
def client():
    return TestClient(app)


class ListSource(ComparableDataSource):
    def __init__(self, miles=(0.1, 0.2, 0.3, 0.4), **overrides):
        self.miles, self.overrides, self.subjects = miles, overrides, []

    def get_comparables(self, subject=None):
        self.subjects.append(subject)
        return [
            {"address": f"{m} St", "rent": 2000 + int(m * 100), "latitude": LAT + m / MILES_PER_DEGREE, "longitude": LON,
             "beds": 3, "baths": 2, "sqft": 1400, **self.overrides}
            for m in self.miles
        ]


def use(source):
    app.dependency_overrides[get_comparable_source] = lambda: source
    return source


def token_of(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def submit(client, source=None, **fields):
    if source is not None:
        use(source)
    return client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(client.get("/ui/")), **fields})


def text_of(html, element_id):
    match = re.search(rf'<(\w+)[^>]*id="{element_id}"[^>]*>(.*?)</\1>', html, re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", match.group(2)))).strip() if match else None


def options_of(html, select_id):
    """[(value, text, selected)] for one select."""
    block = re.search(rf'<select[^>]*id="{select_id}"[^>]*>(.*?)</select>', html, re.S).group(1)
    return [(v, unescape(t), bool(sel)) for v, sel, t in re.findall(r'<option value="([^"]*)"[^>]*?( selected)?>([^<]*)</option>', block)]


def summary_of(html):
    """{label: (value, note)} from the Search criteria panel."""
    block = re.search(r'<section class="card[^"]*" id="search-summary".*?</section>', html, re.S).group(0)
    rows = {}
    for label, body in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", block, re.S):
        value = re.search(r"<span id=\"summary-[^\"]+\"[^>]*>(.*?)</span>", body, re.S).group(1)
        note = re.search(r'<span class="d-block text-body-secondary">(.*?)</span>', body, re.S)
        rows[unescape(label.strip())] = (unescape(value.strip()), unescape(note.group(1).strip()) if note else None)
    return rows


# --- the three controls ------------------------------------------------------------------------------------------------------

def test_the_form_has_the_three_search_controls(client):
    html = client.get("/ui/").text
    for name in ("search_radius_miles", "lookback_days", "property_type"):
        assert re.search(rf'<select class="form-select form-select-lg" id="{name}" name="{name}">', html), name
        assert f'for="{name}"' in html
    assert ">Search radius (miles)</label>" in html and ">Lookback window (days)</label>" in html and ">Building type</label>" in html


def test_radius_options_and_default(client):
    assert options_of(client.get("/ui/").text, "search_radius_miles") == [
        ("0.5", "0.5 miles", False), ("1", "1 mile", True), ("2", "2 miles", False), ("3", "3 miles", False), ("5", "5 miles", False),
    ]


def test_lookback_options_and_default(client):
    assert options_of(client.get("/ui/").text, "lookback_days") == [
        ("30", "30 days", False), ("90", "90 days", True), ("180", "180 days", False), ("365", "365 days", False),
    ]


def test_building_type_options_and_default(client):
    assert options_of(client.get("/ui/").text, "property_type") == [
        ("all", "All building types", True), ("home", "Home (single-family)", False), ("condo", "Condo", False), ("apartment", "Apartment", False),
    ]


def test_the_options_match_what_the_api_accepts(client):
    html = client.get("/ui/").text
    assert [v for v, _, _ in options_of(html, "search_radius_miles")] == [f"{m:g}" for m in RADIUS_OPTIONS_MILES]
    assert [v for v, _, _ in options_of(html, "lookback_days")] == [str(d) for d in LOOKBACK_OPTIONS_DAYS]
    assert [v for v, _, _ in options_of(html, "property_type")] == list(PROPERTY_TYPES)


def test_every_distance_choice_shows_its_unit(client):
    for _, text, _ in options_of(client.get("/ui/").text, "search_radius_miles"):
        assert text.endswith(" mile") or text.endswith(" miles")


def test_each_control_explains_itself(client):
    html = client.get("/ui/").text
    assert "Distance in miles from the property." in html
    assert "How recently listings were posted." in html
    assert "RentCast data only." in html


def test_square_feet_are_optional_on_the_form(client):
    html = client.get("/ui/").text
    assert ">Square feet (optional)</label>" in html
    assert not re.search(r'<input[^>]*id="sqft"[^>]*\brequired\b', html)
    assert "Leave blank to skip size matching; confidence is lowered one level." in html


def test_the_other_property_fields_are_still_required(client):
    html = client.get("/ui/").text
    for name in ("address", "beds", "baths"):
        assert re.search(rf'<input[^>]*id="{name}"[^>]*\brequired\b', html), name


# --- the Search criteria summary --------------------------------------------------------------------------------------------

def test_the_summary_is_above_the_get_valuation_button(client):
    html = client.get("/ui/").text
    assert html.count('id="search-summary"') == 1
    assert html.index('id="search-summary"') < html.index('id="submit-button"')
    assert html.index('id="search-summary"') > html.index('id="property_type"')  # after the controls it describes


def test_the_summary_says_distances_are_miles(client):
    html = client.get("/ui/").text
    assert text_of(html, "search-summary-heading") == "Search criteria"
    assert "Everything that affects this valuation. All distances are in miles." in html


def test_the_summary_lists_every_criterion_in_order(client):
    assert list(summary_of(client.get("/ui/").text)) == SUMMARY_LABELS


def test_the_summary_default_values(client):
    rows = {label: value for label, (value, _) in summary_of(client.get("/ui/").text).items()}
    assert rows["Address"] == "Not entered yet"
    assert rows["Search radius"] == "1 mile" and rows["Distance units"] == "Miles"
    assert rows["Lookback window"] == "Last 90 days" and rows["Building type"] == "All building types"
    assert rows["Minimum comparables"] == "3"
    assert rows["Bedroom filter"] == "Within 1 bedroom of the property"
    assert rows["Bathroom filter"] == "Within 1 bathroom of the property"
    # square feet start empty, so the panel says what submitting right now would do; it changes as a number is typed
    assert rows["Size matching"] == "Not used: square feet were left blank, so confidence is lowered one level"
    assert rows["Outlier handling"] == "Rents below Q1 - 1.5 x IQR or above Q3 + 1.5 x IQR are removed"
    assert rows["Recommended rent"] == "The median rent of the comparables used"
    assert rows["Confidence"] == "Low under 5 comparables, medium 5-9, high 10 or more"


def test_the_summary_states_units_and_the_outlier_minimum(client):
    notes = {label: note for label, (_, note) in summary_of(client.get("/ui/").text).items()}
    assert notes["Distance units"] == "Every distance on this page is in miles."
    assert notes["Search radius"].startswith("The data source is asked for this radius")
    assert notes["Outlier handling"] == "Needs 4 or more comparables; with fewer, none are removed."
    assert notes["Minimum comparables"] == "With fewer, no valuation is shown."
    assert notes["Confidence"] == "Lowered one level when square feet are left blank."


def test_the_summary_derives_its_rules_from_the_engine_not_from_copied_text(client, monkeypatch):
    monkeypatch.setattr(comps, "DEFAULT_SQFT_TOLERANCE", 0.30)
    monkeypatch.setattr(comps, "DEFAULT_BEDROOM_TOLERANCE", 2)
    monkeypatch.setattr(comps, "DEFAULT_BATHROOM_TOLERANCE", 2)
    rows = {label: value for label, (value, _) in summary_of(submit(client, ListSource()).text).items()}  # sqft given
    assert rows["Size matching"] == "Within 30% of the square feet"
    assert rows["Bedroom filter"] == "Within 2 bedroom of the property" and rows["Bathroom filter"] == "Within 2 bathroom of the property"


def test_the_minimum_comparables_follow_the_setting(client, monkeypatch):
    import app.routers.ui as ui_router

    monkeypatch.setattr(ui_router, "MIN_COMPARABLES_REQUIRED", 5)
    assert summary_of(client.get("/ui/").text)["Minimum comparables"][0] == "5"


def test_sample_data_notes(client):
    rows = summary_of(client.get("/ui/").text)
    assert rows["Lookback window"][1] == "Not applied to the built-in sample data, which has no listing dates."
    assert rows["Building type"][1] == "Applies to RentCast data only."
    assert rows["Listings considered"][0] == "26 built-in sample listings (not real data)"
    assert rows["Address lookup"][0] == "Demo addresses only (not real lookups)"


def test_rentcast_notes(client, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("GEOCODER", "census")
    rows = summary_of(client.get("/ui/").text)
    assert rows["Lookback window"][1] == "Listings on the market longer than this are ignored; a listing with no known age is kept."
    assert rows["Building type"][1] is None
    assert rows["Listings considered"][0] == "Active listings, up to 500, as RentCast returns them (most recently seen first)"
    assert rows["Address lookup"][0] == "US Census geocoder (US addresses)"


def test_the_summary_hides_nothing_that_the_engine_does(client):
    """Every rule the engine applies has a row: lookback, distance, bedrooms, bathrooms, size, outliers,
    the minimum, the rent statistic and the confidence rules."""
    rows = summary_of(client.get("/ui/").text)
    for label in ("Lookback window", "Search radius", "Bedroom filter", "Bathroom filter", "Size matching",
                  "Outlier handling", "Minimum comparables", "Recommended rent", "Confidence", "Listings considered"):
        assert label in rows


def test_size_matching_reads_within_20_percent_once_square_feet_are_given(client):
    rows = {label: value for label, (value, _) in summary_of(submit(client, ListSource()).text).items()}
    assert rows["Size matching"] == "Within 20% of the square feet"


def test_the_summary_works_without_scripts(client):
    """The server writes the full, correct text; the script only keeps it in step while editing."""
    html = client.get("/ui/").text
    assert "1 mile" in text_of(html, "summary-radius") and text_of(html, "summary-lookback") == "Last 90 days"


# --- the summary reflects the form --------------------------------------------------------------------------------------------

def test_the_summary_shows_the_submitted_choices(client):
    html = submit(client, ListSource(), search_radius_miles="3", lookback_days="180", property_type="condo", sqft="").text
    rows = {label: value for label, (value, _) in summary_of(html).items()}
    assert rows["Address"] == "123 Main St"
    assert rows["Search radius"] == "3 miles" and rows["Lookback window"] == "Last 180 days" and rows["Building type"] == "Condo"
    assert rows["Size matching"] == "Not used: square feet were left blank, so confidence is lowered one level"


def test_the_selected_choices_stay_selected_after_a_result(client):
    html = submit(client, ListSource(), search_radius_miles="2", lookback_days="365", property_type="apartment").text
    assert [v for v, _, s in options_of(html, "search_radius_miles") if s] == ["2"]
    assert [v for v, _, s in options_of(html, "lookback_days") if s] == ["365"]
    assert [v for v, _, s in options_of(html, "property_type") if s] == ["apartment"]


def test_the_selected_choices_stay_selected_after_an_error(client):
    html = submit(client, ListSource(), search_radius_miles="5", lookback_days="30", property_type="home", beds="").text
    assert 'id="beds-error"' in html
    assert [v for v, _, s in options_of(html, "search_radius_miles") if s] == ["5"]
    assert [v for v, _, s in options_of(html, "lookback_days") if s] == ["30"]
    assert [v for v, _, s in options_of(html, "property_type") if s] == ["home"]
    assert summary_of(html)["Search radius"][0] == "5 miles"


def test_equivalent_numbers_are_normalised(client):
    html = submit(client, ListSource(), search_radius_miles="2.0").text
    assert [v for v, _, s in options_of(html, "search_radius_miles") if s] == ["2"]


# --- validation ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field, value, message",
    [("search_radius_miles", "4", "Choose a search radius from the list (in miles)."),
     ("search_radius_miles", "abc", "Choose a search radius from the list (in miles)."),
     ("search_radius_miles", "-1", "Choose a search radius from the list (in miles)."),
     ("search_radius_miles", "1e0", "Choose a search radius from the list (in miles)."),
     ("lookback_days", "45", "Choose a lookback window from the list (in days)."),
     ("lookback_days", "90.5", "Choose a lookback window from the list (in days)."),
     ("property_type", "house", "Choose a building type from the list.")],
)
def test_tampered_choices_are_refused_with_a_message(client, field, value, message):
    source = use(ListSource())
    response = submit(client, **{field: value})
    assert response.status_code == 422
    assert text_of(response.text, f"{field}-error") == message
    assert source.subjects == []  # nothing was searched


def test_a_refused_choice_shows_the_default_in_the_summary(client):
    rows = {label: value for label, (value, _) in summary_of(submit(client, ListSource(), search_radius_miles="4").text).items()}
    assert rows["Search radius"] == "1 mile"  # never displays something the search would not use


def test_an_older_page_that_posts_only_the_basics_uses_the_defaults(client):
    source = use(ListSource())
    response = client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(client.get("/ui/"))})
    assert response.status_code == 200 and 'id="result-card"' in response.text
    subject = source.subjects[0]
    assert (subject.search_radius_miles, subject.lookback_days, subject.property_type) == (1.0, 90, "all")


def test_a_valuation_without_square_feet_is_accepted_and_saved(client, repository):
    source = use(ListSource(miles=tuple(0.05 * i for i in range(1, 11))))
    response = submit(client, sqft="")
    assert response.status_code == 200 and 'id="result-card"' in response.text
    assert source.subjects[0].sqft is None
    assert repository.get_recent_valuations(1)[0]["sqft"] is None


# --- results show the search ---------------------------------------------------------------------------------------------------

def test_a_result_shows_the_search_it_used(client):
    html = submit(client, ListSource(miles=(1.5, 1.6, 1.7, 1.8)), search_radius_miles="2", lookback_days="180", property_type="condo").text
    assert text_of(html, "search-used") == (
        "Search used: Within 2 miles · listed in the last 180 days · Condo · matched on size · minimum 3 comparables"
    )


def test_the_funnel_names_the_radius_and_the_lookback(client):
    html = submit(client, ListSource(miles=(1.5, 1.6, 1.7, 1.8)), search_radius_miles="2", lookback_days="180").text
    assert "Within 2 miles of the property" in html and "Listed within the last 180 days" in html


def test_the_funnel_label_changes_without_square_feet(client):
    html = submit(client, ListSource(), sqft="").text
    assert "Similar bedrooms and bathrooms" in html and "Similar bedrooms, bathrooms and size" not in html


def test_confidence_is_lowered_and_explained_without_square_feet(client):
    source = ListSource(miles=tuple(0.05 * i for i in range(1, 11)))
    with_sqft = submit(TestClient(app), source)
    without = submit(TestClient(app), source, sqft="")
    assert 'data-confidence="high"' in with_sqft.text and 'id="confidence-notes"' not in with_sqft.text
    assert 'data-confidence="medium"' in without.text
    assert "confidence was lowered one level" in text_of(without.text, "confidence-notes")
    assert "not matched on size" in text_of(without.text, "search-used")


def test_a_search_with_no_nearby_listing_says_how_far_the_nearest_is(client):
    html = submit(client, ListSource(miles=(1.8, 2.4, 3.0)), search_radius_miles="1").text
    assert 'id="insufficient-data"' in html
    assert "The nearest listing found is 1.8 miles away, so try a larger search radius." in text_of(html, "insufficient-explanation")
    assert text_of(html, "search-used").startswith("Search used: Within 1 mile")


def test_a_larger_radius_then_gives_a_valuation(client):
    source = ListSource(miles=(1.8, 1.9, 1.95, 1.99))
    assert 'id="insufficient-data"' in submit(client, source, search_radius_miles="1").text
    assert 'id="result-card"' in submit(TestClient(app), source, search_radius_miles="2").text


def test_the_insufficient_funnel_shows_the_radius(client):
    html = submit(client, ListSource(miles=(1.8, 2.4, 3.0)), search_radius_miles="0.5").text
    assert "Within 0.5 miles of the property" in html


def test_listings_that_are_all_too_old_suggest_a_longer_lookback(client):
    html = submit(client, ListSource(days_on_market=200), lookback_days="90").text
    assert "Try a longer lookback window." in text_of(html, "insufficient-explanation")
    assert "listed within the last 90 days" in text_of(html, "insufficient-explanation")


def test_the_nearest_listing_is_not_mentioned_when_nothing_was_fetched(client):
    html = submit(client, ListSource(miles=())).text
    assert "returned no listings" in text_of(html, "insufficient-explanation")
    assert "nearest listing" not in text_of(html, "insufficient-explanation")


# --- hooks for the page script -------------------------------------------------------------------------------------------------

def test_the_summary_rows_have_ids_the_script_updates(client):
    html = client.get("/ui/").text
    for key in ("address", "radius", "lookback", "property_type", "size"):
        assert f'id="summary-{key}"' in html
    assert re.search(r'id="summary-size" data-with="Within 20% of the square feet" data-without="Not used: square feet were left blank', html)


def test_the_options_carry_the_text_the_script_shows(client):
    html = client.get("/ui/").text
    assert '<option value="90" data-summary="Last 90 days" selected>90 days</option>' in html
    assert '<option value="0.5" data-summary="0.5 miles">0.5 miles</option>' in html
    assert '<option value="home" data-summary="Home (single-family)">Home (single-family)</option>' in html


def test_the_script_updates_the_summary_as_the_form_changes(client):
    script = client.get("/ui/static/js/app.js").text
    for needle in ('"summary-address"', '"summary-radius"', '"summary-lookback"', '"summary-property_type"',
                   '"summary-size"', "data-with", "data-without", "data-summary", 'addEventListener("input"', 'addEventListener("change"',
                   '"search_radius_miles"', '"lookback_days"', '"property_type"', '"sqft"'):
        assert needle in script, needle
    assert "Not entered yet" in script  # the same text the server shows for an empty address


# --- mobile layout --------------------------------------------------------------------------------------------------------------

def test_the_new_controls_stack_on_phones_and_share_a_row_from_sm_up(client):
    html = client.get("/ui/").text
    for name in ("search_radius_miles", "lookback_days", "property_type"):
        assert re.search(rf'<div class="col-12 col-sm-4">\s*<label for="{name}"', html), name


def test_the_selects_are_large_touch_targets(client):
    html = client.get("/ui/").text
    assert html.count('class="form-select form-select-lg') == 3


def test_the_summary_stacks_label_over_value_on_phones(client):
    html = client.get("/ui/").text
    assert html.count('<dt class="col-12 col-sm-4 fw-semibold">') == len(SUMMARY_LABELS)
    assert html.count('<dd class="col-12 col-sm-8 mb-2 mb-sm-0"') == len(SUMMARY_LABELS)


def test_no_fixed_widths_or_tables_that_force_sideways_scrolling(client):
    for html in (client.get("/ui/").text, submit(TestClient(app), ListSource()).text, submit(TestClient(app), ListSource(miles=(3.0, 4.0))).text):
        assert "<table" not in html
        for style in re.findall(r'style="([^"]*)"', html):
            assert "px" not in style.replace("height: .4rem", "")


def test_the_summary_is_a_card_inside_the_form_column(client):
    html = client.get("/ui/").text
    assert re.search(r'<div class="col-12">\s*<section class="card border-secondary-subtle mb-1" id="search-summary"', html)
