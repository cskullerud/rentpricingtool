# Changelog

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
