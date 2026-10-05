"""Parsing and validation of the valuation form.

Validation here is for the person using the page: it produces one plain-language message per
field and keeps exactly what they typed so the form can be shown again. The API's own rules
(ValuationRequest) are unchanged; a valid form is converted into that same request object.
"""
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.schemas import ValuationRequest
from app.services.search_options import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PROPERTY_TYPE,
    DEFAULT_RADIUS_MILES,
    LOOKBACK_OPTIONS_DAYS,
    PROPERTY_TYPES,
    RADIUS_OPTIONS_MILES,
)

FIELDS = (
    "address", "beds", "baths", "sqft", "latitude", "longitude",
    "search_radius_miles", "lookback_days", "property_type",
)
# What a select shows when it was not submitted (an older page, or a script posting only the basics).
SELECT_DEFAULTS = {
    "search_radius_miles": f"{DEFAULT_RADIUS_MILES:g}",
    "lookback_days": str(DEFAULT_LOOKBACK_DAYS),
    "property_type": DEFAULT_PROPERTY_TYPE,
}

MAX_ADDRESS_LENGTH = 200
MAX_BEDS = 50
MAX_BATHS = 50
MAX_SQFT = 100_000

_WHOLE = re.compile(r"^\d{1,6}$")
_DECIMAL = re.compile(r"^\d{1,6}(\.\d{1,3})?$")
_COORDINATE = re.compile(r"^-?\d{1,3}(\.\d{1,10})?$")


@dataclass
class ValuationForm:
    values: dict[str, str]  # what was typed (trimmed), for showing the form again
    errors: dict[str, str] = field(default_factory=dict)  # field name -> message
    request: ValuationRequest | None = None  # set only when the form is valid
    expand_coordinates: bool = False  # open the coordinates section (e.g. after an address lookup failure)

    @property
    def is_valid(self) -> bool:
        return self.request is not None and not self.errors

    @property
    def show_coordinates(self) -> bool:
        """Whether the optional coordinates section should start open."""
        return self.expand_coordinates or bool(
            self.values.get("latitude") or self.values.get("longitude")
            or "latitude" in self.errors or "longitude" in self.errors
        )

    @classmethod
    def blank(cls) -> "ValuationForm":
        return cls(values={name: SELECT_DEFAULTS.get(name, "") for name in FIELDS})

    @classmethod
    def from_form(cls, data: Mapping) -> "ValuationForm":
        """Read and validate submitted form data. Non-text values (file uploads) count as empty."""
        values = {}
        for name in FIELDS:
            raw = data.get(name, "")
            values[name] = raw.strip() if isinstance(raw, str) else ""
            if name in SELECT_DEFAULTS and not values[name]:
                values[name] = SELECT_DEFAULTS[name]
        form = cls(values=values)
        form._validate()
        return form

    # ---- validation ---------------------------------------------------------------

    def _validate(self) -> None:
        v = self.values
        address = v["address"]
        if not address:
            self.errors["address"] = "Enter the property's address."
        elif len(address) > MAX_ADDRESS_LENGTH:
            self.errors["address"] = f"The address must be {MAX_ADDRESS_LENGTH} characters or fewer."

        beds = self._whole("beds", v["beds"], "Enter the number of bedrooms.", "bedrooms", MAX_BEDS, minimum=0)
        baths = self._decimal("baths", v["baths"], "Enter the number of bathrooms.", "bathrooms", MAX_BATHS)
        # Square feet are optional: blank means "do not match on size" (confidence is lowered instead).
        sqft = None
        sqft_text = v["sqft"].replace(",", "").replace(" ", "")
        if sqft_text:
            sqft = self._whole("sqft", sqft_text, "", "square footage", MAX_SQFT, minimum=1)
        latitude, longitude = self._coordinates(v["latitude"], v["longitude"])
        radius = self._radius(v["search_radius_miles"])
        lookback = self._lookback(v["lookback_days"])
        property_type = self._property_type(v["property_type"])

        if self.errors:
            return
        try:
            self.request = ValuationRequest(
                address=address, beds=beds, baths=baths, sqft=sqft, latitude=latitude, longitude=longitude,
                search_radius_miles=radius, lookback_days=lookback, property_type=property_type,
            )
        except ValidationError:  # should not happen after the checks above; never show internals
            self.errors["address"] = "Check the details and try again."

    def _radius(self, raw: str) -> float:
        """The search radius in miles: one of the offered choices. The text is normalised ("1.0" -> "1")."""
        if _DECIMAL.match(raw) and float(raw) in RADIUS_OPTIONS_MILES:
            self.values["search_radius_miles"] = f"{float(raw):g}"
            return float(raw)
        self.errors["search_radius_miles"] = "Choose a search radius from the list (in miles)."
        return DEFAULT_RADIUS_MILES

    def _lookback(self, raw: str) -> int:
        if _WHOLE.match(raw) and int(raw) in LOOKBACK_OPTIONS_DAYS:
            self.values["lookback_days"] = str(int(raw))
            return int(raw)
        self.errors["lookback_days"] = "Choose a lookback window from the list (in days)."
        return DEFAULT_LOOKBACK_DAYS

    def _property_type(self, raw: str) -> str:
        key = raw.lower()
        if key in PROPERTY_TYPES:
            self.values["property_type"] = key
            return key
        self.errors["property_type"] = "Choose a building type from the list."
        return DEFAULT_PROPERTY_TYPE

    def _whole(self, name, raw, missing, label, maximum, minimum) -> int | None:
        if not raw:
            self.errors[name] = missing
            return None
        if not _WHOLE.match(raw):
            self.errors[name] = f"Enter {label} as a whole number, like 3."
            return None
        number = int(raw)
        if number < minimum:
            self.errors[name] = f"The {label} must be at least {minimum}."
        elif number > maximum:
            self.errors[name] = f"The {label} must be {maximum:,} or fewer."
        else:
            return number
        return None

    def _decimal(self, name, raw, missing, label, maximum) -> float | None:
        if not raw:
            self.errors[name] = missing
            return None
        if not _DECIMAL.match(raw):
            self.errors[name] = f"Enter {label} as a number, like 2 or 1.5."
            return None
        number = float(raw)
        if not math.isfinite(number) or number > maximum:
            self.errors[name] = f"The {label} must be {maximum:,} or fewer."
            return None
        return number

    def _coordinates(self, lat_raw: str, lon_raw: str) -> tuple[float | None, float | None]:
        if not lat_raw and not lon_raw:
            return None, None
        if bool(lat_raw) != bool(lon_raw):
            missing = "longitude" if lat_raw else "latitude"
            self.errors[missing] = "Enter both latitude and longitude, or leave both empty."
            return None, None
        latitude = self._coordinate("latitude", lat_raw, 90)
        longitude = self._coordinate("longitude", lon_raw, 180)
        if latitude is None or longitude is None:
            return None, None
        return latitude, longitude

    def _coordinate(self, name: str, raw: str, limit: int) -> float | None:
        if not _COORDINATE.match(raw):
            self.errors[name] = f"Enter the {name} as a decimal number, like 32.7678."
            return None
        number = float(raw)
        if not -limit <= number <= limit:
            self.errors[name] = f"The {name} must be between -{limit} and {limit}."
            return None
        return number
