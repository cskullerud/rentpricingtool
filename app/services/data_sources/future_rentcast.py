# TODO: RentCast provider (planned, not implemented).
#
# - Add a RentCastComparableSource(ComparableDataSource) class here.
# - get_comparables() should call the RentCast API and map each listing to the Comparable
#   shape: address, rent, latitude, longitude, beds, baths, sqft.
# - Listings carry coordinates, so map them to latitude/longitude directly; distance is
#   calculated from them by the filters (app/services/geo.py).
# - Query around the subject's coordinates, which the request provides or geocoding will.
# - Read the API key from an environment variable (see app/config.py); never hardcode it.
# - Handle rate limits, timeouts and empty results; cache responses where sensible.
# - Tests must stub the HTTP layer: the test suite makes no network calls.
