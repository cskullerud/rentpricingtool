# TODO: Google geocoding provider (planned, not implemented).
#
# - Add a GoogleGeocoder(Geocoder) class here whose geocode(address) calls the Google
#   Geocoding API and returns {"latitude": ..., "longitude": ...}.
# - Raise AddressNotFoundError when Google returns ZERO_RESULTS, or only a partial match
#   that is too imprecise to price (for example a whole city instead of a street address).
# - Read the API key from an environment variable (see app/config.py); never hardcode it.
# - Decide how to treat other failures (quota, timeouts, network errors): they are not
#   "address not found", so they need their own error and a different HTTP status.
# - Cache results (for example in the SQLite database from Phase 5) to save quota.
# - Tests must stub the HTTP layer: the test suite makes no network calls.
# - Other providers (Mapbox, Nominatim, a local geocoder, ...) can follow the same shape.
