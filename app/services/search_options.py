"""The choices a valuation search offers, in one place (UI, API validation and the data source
all read from here). Distances are always miles."""

DISTANCE_UNITS = "miles"

RADIUS_OPTIONS_MILES = (0.5, 1.0, 2.0, 3.0, 5.0)
DEFAULT_RADIUS_MILES = 1.0

LOOKBACK_OPTIONS_DAYS = (30, 90, 180, 365)
DEFAULT_LOOKBACK_DAYS = 90

# Building type -> (label shown to people, the value RentCast's `propertyType` filter takes, or
# None for "no filter"). RentCast supports Single Family, Condo, Townhouse, Manufactured,
# Multi-Family and Apartment; "Home" is its Single Family. The other three are not offered, so
# "All" is the way to include them.
PROPERTY_TYPES: dict[str, tuple[str, str | None]] = {
    "all": ("All building types", None),
    "home": ("Home (single-family)", "Single Family"),
    "condo": ("Condo", "Condo"),
    "apartment": ("Apartment", "Apartment"),
}
DEFAULT_PROPERTY_TYPE = "all"


def format_miles(miles: float) -> str:
    """0.5 -> '0.5 miles', 1 -> '1 mile', 2 -> '2 miles'. The unit is always shown."""
    number = f"{miles:g}"
    return f"{number} mile" if miles == 1 else f"{number} miles"


def format_days(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


def property_type_label(key: str) -> str:
    return PROPERTY_TYPES[key][0]


def rentcast_property_type(key: str) -> str | None:
    """The RentCast `propertyType` value for a building type, or None for no filter."""
    return PROPERTY_TYPES[key][1]
