# Changelog

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
