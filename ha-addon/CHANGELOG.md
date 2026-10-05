# Changelog

## 0.3.1

- The `valuation_funnel` line in the Log tab now includes `building_type` (`all`, `home`, `condo`
  or `apartment`), for successful and "not enough data" valuations alike, so each valuation's
  search can be read back from the log.

## 0.3.0

- **Search controls on the form:** Search radius (0.5, 1, 2, 3 or 5 **miles**, default 1), Lookback
  window (30, 90, 180 or 365 days, default 90) and Building type (All, Home, Condo, Apartment).
- **The search radius is now the radius used to ask RentCast**, as well as the radius used to
  filter listings. Before, RentCast was asked for 5 miles (and at most 100 listings, in its own
  order, newest first) while only 1 mile was kept, so valuations could find "0 within 1 mile".
  RentCast is now asked for up to 500 listings.
- **Square feet are optional.** Without them the size filter is skipped and confidence is lowered
  one level, with a note explaining why.
- **Search criteria panel** above the Get valuation button lists every setting and rule that
  affects a valuation (with units), and a one-line summary is shown with each result.
- When nothing is close enough, the page says how far away the nearest listing is.
- The Log tab shows the search radius, limit and nearest listing for each RentCast request, and
  warns when a response reaches the limit (`RentCast response reached limit (500); nearby
  listings may be missing.`).
- The `RENTCAST_RADIUS_MILES` setting is gone (the radius is chosen per valuation).
- Valuations ask RentCast for different data only when the radius or building type changes;
  changing the lookback uses the same cached listings.

## 0.2.0

- Address lookup: addresses are now turned into locations with the free US Census geocoder, so a
  valuation works from a street address, city, state and ZIP code. New option **Address lookup**
  (`census` by default; `mock` is the old demo behaviour).
- Looked-up addresses are cached for 90 days in the app's database (and "not found" for one
  day), so repeat valuations make no lookup and already-seen addresses keep working if the
  service is down.
- Apartment, unit, suite and `#number` designators are removed before the lookup.
- A lookup that fails because the service is down or busy now says so, instead of "address not
  found", and never uses a RentCast request.
- The Log tab shows `Geocoder: census` and `Geocode cache: enabled` at start, and warns if
  RentCast is on with the mock address lookup.

## 0.1.1

- Fix: the page's stylesheet and scripts (Bootstrap, app.css, app.js) returned 404 when opened
  through Home Assistant ingress, so the page showed unstyled and the coordinates section and
  double-submit guard did not work. Static files now load through ingress, and directly.

## 0.1.0

- First release as a Home Assistant app.
- Web UI under ingress, opening from the sidebar (administrators only).
- Data source option: built-in sample data (default) or live RentCast data.
- Connections accepted only from the Home Assistant Supervisor (ingress); no published ports.
- Fresh database in the app's own `/data` storage. No automatic migration of an earlier
  database (see the documentation for the optional manual steps).
