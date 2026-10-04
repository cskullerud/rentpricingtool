"""Address clean-up for the geocoder and its cache. Pure functions, no I/O.

Two different jobs:
- `strip_unit()` and `clean_address()` prepare the text sent to a geocoder. Street geocoders
  place an address on its street, so "Apt 4B" and "#12" only get in the way.
- `cache_key()` makes two spellings of the same address share one cached answer.
"""
import re

# "Apt 4B", "Apartment 12", "Unit B", "Suite 100", "Ste 5", "#12", "# 12" (with the separator before it).
_UNIT = re.compile(
    r"(?:^|[\s,]+)(?:(?:apartment|apt|unit|suite|ste)\b\.?\s*#?\s*|#\s*)(?P<token>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)",
    re.IGNORECASE,
)
# Street-type words that can follow "Unit"/"Suite" in a street name ("123 Unit St"): not a unit number.
_STREET_WORDS = {"st", "rd", "dr", "ln", "ct", "pl", "cir", "ave", "blvd", "hwy", "way", "ter", "trl", "pkwy", "sq"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _is_unit_token(token: str) -> bool:
    """A unit designator is followed by something like 4B, 12, B or 3-A, not by a plain word."""
    if token.casefold() in _STREET_WORDS:
        return False
    return any(ch.isdigit() for ch in token) or len(token) <= 2


def clean_address(address: str) -> str:
    """Trim, drop control characters and collapse whitespace. Case and punctuation are kept."""
    text = _CONTROL.sub(" ", address or "")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,")


def strip_unit(address: str) -> str:
    """Remove apartment, unit, suite and #number designators: "123 Main St Apt 4B, San Diego, CA"
    becomes "123 Main St, San Diego, CA". A word that merely follows "Unit" in a street name is kept."""

    def drop(match: re.Match) -> str:
        return "" if _is_unit_token(match.group("token")) else match.group(0)

    return clean_address(_UNIT.sub(drop, clean_address(address)))


def cache_key(address: str) -> str:
    """The same text for spellings that mean the same address: case, spacing, periods,
    'California' vs 'CA' and any unit designator are ignored."""
    text = strip_unit(address).casefold().replace(".", "")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return re.sub(r",\s*california\b", ", ca", text)
