import re

from app.services.geocoding.base import AddressNotFoundError, Coordinates, Geocoder

# Fictional addresses with coordinates around La Mesa / San Diego, CA. No network calls.
# "123 Main St, La Mesa, CA" sits exactly on the mock subject point that earlier phases used
# by default (app/config.py), so valuations for it are unchanged. The downtown and coastal
# San Diego addresses are miles from the mock comparables, so valuing them finds no matches.
MOCK_ADDRESSES: dict[str, Coordinates] = {
    # La Mesa (inside the mock comparable cluster)
    "123 Main St, La Mesa, CA": {"latitude": 32.7678, "longitude": -117.0231},
    "456 Palm Ave, La Mesa, CA": {"latitude": 32.7701, "longitude": -117.0160},
    "210 Spring St, La Mesa, CA": {"latitude": 32.7655, "longitude": -117.0289},
    "77 Allison Ave, La Mesa, CA": {"latitude": 32.7714, "longitude": -117.0272},
    "8500 La Mesa Blvd, La Mesa, CA": {"latitude": 32.7683, "longitude": -117.0102},
    "100 University Ave, La Mesa, CA": {"latitude": 32.7620, "longitude": -117.0205},
    "3300 Lake Murray Blvd, La Mesa, CA": {"latitude": 32.7745, "longitude": -117.0400},
    # San Diego
    "5500 College Ave, San Diego, CA": {"latitude": 32.7744, "longitude": -117.0712},
    "4500 El Cajon Blvd, San Diego, CA": {"latitude": 32.7580, "longitude": -117.0970},
    "100 University Ave, San Diego, CA": {"latitude": 32.7482, "longitude": -117.1486},
    "789 Broadway, San Diego, CA": {"latitude": 32.7153, "longitude": -117.1573},
    "1200 Harbor Dr, San Diego, CA": {"latitude": 32.7062, "longitude": -117.1704},
    "4545 Mission Blvd, San Diego, CA": {"latitude": 32.7945, "longitude": -117.2547},
}


def _normalize(address: str) -> str:
    """Case, spacing, periods and 'California' vs 'CA' don't matter when matching."""
    text = address.casefold().replace(".", "")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return re.sub(r",\s*california$", ", ca", text)


class MockGeocoder(Geocoder):
    """Looks addresses up in a small fixed table.

    Matching ignores case, spacing and periods. A shorter address also matches when it is
    the start of exactly one known address: "123 Main St" and "123 Main St, La Mesa" both
    find "123 Main St, La Mesa, CA". If it starts several (the same street in two cities),
    the address is ambiguous and raises AddressNotFoundError.
    """

    def __init__(self) -> None:
        self._table = {_normalize(a): dict(c) for a, c in MOCK_ADDRESSES.items()}

    def geocode(self, address: str) -> Coordinates:
        query = _normalize(address or "")
        if not query:
            raise AddressNotFoundError(address, "address is empty")
        if query in self._table:
            return dict(self._table[query])  # type: ignore[return-value]
        matches = [key for key in self._table if key.startswith(query + ",")]
        if len(matches) == 1:
            return dict(self._table[matches[0]])  # type: ignore[return-value]
        if len(matches) > 1:
            raise AddressNotFoundError(address, "address is ambiguous; include the city and state")
        raise AddressNotFoundError(address)
