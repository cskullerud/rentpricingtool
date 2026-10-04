# TODO: RentCast provider (planned, not implemented).
#
# - Add a RentCastComparableSource(ComparableDataSource) class here.
# - get_comparables() should call the RentCast API and map each listing to the Comparable
#   shape: address, rent, distance_miles, beds, baths, sqft.
# - distance_miles needs the subject's location, so this depends on the geocoding work.
# - Read the API key from an environment variable (see app/config.py); never hardcode it.
# - Handle rate limits, timeouts and empty results; cache responses where sensible.
# - Tests must stub the HTTP layer: the test suite makes no network calls.
