# Architecture

Status: **Phase 6 - geocoding abstraction (mock geocoder only), on top of SQLite persistence, coordinate-based distance and a data-source abstraction, running on mock comparable data.** This document describes
what exists today; planned work is listed at the end.

## Overview

Rent Pricing Tool is a small FastAPI service. A client posts a subject property
(address, beds, baths, sqft) and gets back a rent valuation: a recommended rent, the
25th/50th/75th percentiles, and the average of the comparable properties it was based on.

Valuations are in-process and deterministic: there is no network call and no external
service. The comparable properties come from a `ComparableDataSource`; the only
implementation today is a static mock dataset. Each successful valuation is also recorded
in a local SQLite file, which `GET /stats` and `GET /history` read back. A subject's
location comes from the coordinates in the request or, failing that, from a `Geocoder`
that turns the address into coordinates (a mock today).

## Request flow

```
Client
  |  POST /valuation  {address, beds, baths, sqft, [latitude, longitude]}
  v
app/main.py                    FastAPI app, mounts the routers, creates the database at startup
  v
app/routers/valuation.py       validates the body (ValuationRequest), picks the data source,
  |                            repository and geocoder, calls the engine, maps the result to
  |                            ValuationResponse and errors to HTTP
  v
app/services/valuation_engine.py   run_valuation(subject, source, repository, geocoder)
  |-- locate the subject         coordinates in the request, else geocoder.geocode(address)
  |-- source.get_comparables()   ComparableDataSource (injected by the router)
  |-- comparables.py             filter to similar properties
  |-- statistics.py              remove outliers, compute percentiles and average
  '-- repository.save_valuation_request()   record the result (best effort)
  v
JSON response

GET /stats, GET /history  ->  routers/stats.py, routers/history.py  ->  ValuationRepository
```

## Layout

```
app/
  main.py                 App creation and the GET / health endpoint
  config.py               APP_NAME, VERSION, ENVIRONMENT, DATABASE_PATH,
                          MIN_COMPARABLES_REQUIRED (from environment variables) and the
                          default subject coordinate
  schemas.py              Pydantic request/response models
  routers/
    valuation.py          POST /valuation
    stats.py              GET /stats
    history.py            GET /history
  persistence/
    database.py           DatabaseManager (SQLite connections and schema), PersistenceError
    repositories.py       ValuationRepository: all SQL lives here
  services/
    statistics.py         Percentiles, average, standard deviation, IQR outlier removal
    comparables.py        The four filters (distance, bedrooms, bathrooms, sqft)
    geo.py                Great-circle distance: haversine_distance(), miles_between_points()
    valuation_engine.py   Orchestrates the valuation workflow
    geocoding/
      base.py             Geocoder interface, Coordinates type, AddressNotFoundError
      mock_geocoder.py    MockGeocoder and its table of 13 fictional addresses
      future_google.py    Stub: planned Google provider (TODO comments only)
    data_sources/
      base.py             Comparable type and the ComparableDataSource interface
      mock_source.py      MockComparableSource and the 26-record mock dataset
      rentcast_source.py  RentCastComparableSource: active rental listings from RentCast, cached
      csv_source.py       CsvComparableSource placeholder (raises NotImplementedError)
      provider_config.py  ProviderType and get_provider_type() (reads DATA_PROVIDER on each call)
      provider_registry.py  get_provider(): builds the source for the configured provider
tests/                    pytest suite (API, services)
docs/                     Documentation
scripts/                  Helper scripts (empty)
data/                     Local data, contents git-ignored (holds rentpricingtool.db once
                          the app has run)
```

## Layers and responsibilities

| Layer | Module | Responsibility | Knows about |
|---|---|---|---|
| HTTP | `routers/` | Parse and validate input, choose the data source and repository, translate errors to status codes | schemas, engine, data sources, persistence |
| Contract | `schemas.py` | Shape of requests and responses | nothing |
| Domain | `services/valuation_engine.py` | The valuation workflow | schemas (request type), `ComparableDataSource` and `Geocoder` (interfaces only), comparables, statistics, `ValuationRepository` (to save results) |
| Persistence | `persistence/` | Storing and reading valuation history in SQLite | schemas, config |
| Domain | `services/comparables.py` | Filtering comparables | `Comparable` type, `geo` |
| Domain | `services/geo.py` | Distance between coordinates | nothing |
| Domain | `services/geocoding/` | Turning an address into coordinates | nothing outside the package |
| Data | `services/data_sources/` | Supplying comparable properties | nothing outside the package |
| Domain | `services/statistics.py` | Numeric helpers | nothing |
| Config | `config.py` | Settings from the environment, default subject coordinate | nothing |

The routers depend on services, and services never import from routers. The engine depends
on the `ComparableDataSource` interface, never on a concrete provider; only the router
names one. `statistics.py` and `comparables.py` are small modules that can be tested on
their own.

## Valuation algorithm

`run_valuation(subject)` in `valuation_engine.py`:

1. **Locate the subject.** Use the request's coordinates if it has them; otherwise geocode
   its address. Then **get** the comparables from the injected `ComparableDataSource`.
2. **Filter** to properties similar to the subject. All four filters must pass.

   | Filter | Rule | Default |
   |---|---|---|
   | Distance | great-circle miles from the subject's coordinates `<= max`, calculated per comparable | 1.0 mile |
   | Bedrooms | within +/- tolerance of the subject | 1 bedroom |
   | Bathrooms | within +/- tolerance of the subject | 1 bathroom |
   | Square feet | within +/- a fraction of the subject's sqft | 20% (inclusive) |

3. **Extract** the rents of what remains.
4. **Remove outliers** with the IQR rule: drop rents below `Q1 - 1.5 x IQR` or above
   `Q3 + 1.5 x IQR`. With fewer than 4 values nothing is removed. Then **check the
   minimum**: if fewer than `MIN_COMPARABLES_REQUIRED` (default 3) rents remain, raise
   `InsufficientDataError` instead of pricing (see Minimum comparables below).
5. **Calculate** the 25th, 50th and 75th percentiles (linear interpolation between ranks)
   and the average.
6. **Return** the result, with figures rounded to whole dollars:

   ```json
   {
     "comparable_count": 16,
     "p25": 2419,
     "median": 2512,
     "p75": 2606,
     "average": 2505,
     "recommended_rent": 2512,
     "confidence": "high",
     "funnel": {
       "comparables_fetched": 26,
       "comparables_after_distance_filter": 24,
       "comparables_after_attribute_filter": 18,
       "comparables_after_outlier_filter": 16,
       "comparables_used": 16
     }
   }
   ```

`recommended_rent` is the median. `comparable_count` is the number of comparables used for
pricing, i.e. after both filtering and outlier removal.

### Quality diagnostics

Every valuation reports how good its evidence is (`services/quality.py`):

- **`funnel`** counts the comparables left after each stage: `comparables_fetched` (what the
  source returned), `comparables_after_distance_filter`,
  `comparables_after_attribute_filter` (bedrooms, bathrooms and square footage together),
  `comparables_after_outlier_filter`, and `comparables_used` (what was priced; always equal
  to `comparable_count`). A comparable is counted as removed at the first stage it fails.
  The funnel shows where listings were lost: for example, many fetched but few within a mile
  means the provider's search radius is wider than the engine's distance filter.
- **`confidence`** is based on `comparables_used`: **low** below 5 (3-4 with the default
  minimum), **medium** 5-9, **high** 10 or more. It reflects how many comparables priced the
  result, not how tightly their rents cluster.

The same `funnel` is included in the `insufficient_data` 404 body (with no `confidence`),
which is where it is most useful. Neither field is stored in the history table.

Each valuation, successful or not, writes one log line from `valuation_engine`, for example:

```
valuation_funnel status=ok fetched=26 after_distance=24 after_attributes=18 after_outliers=16 used=16 minimum=3 confidence=high
```

The same values are attached to the log record as attributes (`record.funnel`,
`record.confidence`, `record.valuation_status`, `record.minimum_required`) for JSON or other
structured handlers. The address and other request details are never logged. Requests that
fail before comparables are fetched (such as an unknown address) log no funnel.

### Comparable data

Each comparable has `address`, `rent`, `latitude`, `longitude`, `beds`, `baths` and `sqft`
(the `Comparable` type in `data_sources/base.py`). The mock dataset has 26 of them, with
coordinates clustered around La Mesa / San Diego, CA. Street names are fictional. Distance
is not stored: it is calculated from the coordinates when filtering. The set deliberately contains two rent outliers (900 and 8000)
that pass the filters, plus properties the filters should exclude, so every stage of the
pipeline is exercised.

## Web UI

Server-rendered HTML with Jinja2 and Bootstrap 5.3, under the `/ui` prefix. No JavaScript
framework, no build step and no CDN: Bootstrap's CSS and JS are vendored in
`app/static/vendor/bootstrap` (pinned version and SHA-256 checksums in its `VERSION` file).

```
app/
  routers/ui.py        HTML routes (hidden from OpenAPI): GET /ui, /ui/, POST /ui/valuation, /ui/history
  ui/
    templating.py      Jinja2Templates, the ui_url() URL helper, the data-source badge, render()
    ingress.py         IngressMiddleware: X-Ingress-Path -> scope["ingress_prefix"] (not root_path)
    forms.py           ValuationForm: parsing, per-field messages, keeps what was typed
    csrf.py            cookie-bound CSRF tokens
    viewmodels.py      money(), confidence styles, funnel rows, the insufficient-data explanation
    errors.py          HTML error pages (404, 405, provider problems), is_ui_page_request()
  templates/           base.html, valuation_form.html, history.html, error.html
    partials/          _form_fields, _result_card, _confidence_badge, _funnel, _insufficient, _alert
  static/              mounted at /ui/static: vendor/bootstrap, css/app.css, js/app.js
```

- **Ingress-safe URLs.** Home Assistant ingress serves the app under a prefix and reports it in
  `X-Ingress-Path`. The middleware records a *validated* value (single leading `/`, only letters,
  digits and `_ . - /`, no `..`, at most 200 characters; anything else is ignored) as
  `scope["ingress_prefix"]`, and templates build every link, form action and static URL with
  `{{ ui_url('/ui/...') }}`. Without the header the prefix is empty. The JSON API is not affected.
  **The prefix is deliberately not put in the ASGI `root_path`.** Home Assistant strips the
  prefix before forwarding (the app sees `/ui/...`), and Starlette's mounts assume `root_path` is
  a prefix of the path, so the static files returned 404 through ingress (found in the deployed
  app, fixed in 0.1.1). Instead the middleware makes the path the same however a proxy forwards
  it: a prefix still present is removed, one already removed is left alone, so routing never sees
  it. `test_ui_infrastructure.py` loads every URL on the page through a prefix-stripping proxy.
- **The valuation form.** `POST /ui/valuation` reads the form (`forms.py`), builds the same
  `ValuationRequest` the API uses, and calls the same `run_valuation()` with the same
  dependencies (`get_comparable_source`, `get_geocoder`, `get_repository`), off the event loop.
  There is no valuation logic in the UI, and each successful valuation is saved to history just
  like an API call. The page can show:
  - a **result** (recommended rent, confidence badge, comparable count, 25th/median/75th
    percentile and average, and the funnel) with the form below for another run;
  - **insufficient data** (found vs. required, an explanation of the first stage that ran
    short, the funnel, and no confidence badge);
  - **field errors** (HTTP 422), one plain message per field, with everything typed kept;
  - an **alert** for an expired form (403), an unknown address (422, with the coordinates
    section opened), or a data-source problem (502/503) or unexpected failure (500), always a
    generic message with no technical detail (the detail is logged).
  The form's own rules (address required, bedrooms >= 0, bathrooms >= 0, square feet > 0,
  latitude and longitude together or not at all) are for the user; the API's rules are unchanged.
- **CSRF.** A token alone would not stop an attacker whose server fetches one, so it is bound to
  the visitor: the server sets an HttpOnly, SameSite=Lax cookie (`rpt_csrf`, path = the ingress
  prefix + `/ui`, 8 hours, `Secure` only behind HTTPS) and the form carries an HMAC of its value.
  A cross-site page can neither read the cookie nor have the browser send it with a cross-site
  POST. The HMAC key is `UI_SECRET_KEY` or, if unset, random per process, in which case a form
  left open across a restart is rejected (403) and shown again with its entries and a fresh
  token. The token is checked before the form is validated, so a rejected post runs nothing.
  This protects the paid data source: without it, another web page could make the visitor's
  browser trigger valuations.
- **Error pages.** The app-wide handlers return an HTML page for paths under `/ui` (but not
  `/ui/static`) and exactly the same JSON as before for everything else. This covers errors
  raised while the router builds its dependencies, which no route code can catch.
- **Double submits.** `static/js/app.js` disables the button after the first submit, so a
  valuation (and a possible data-plan request) is not run twice.
- **Mobile.** Fields stack on phones (`col-12`) and sit in a row from the `sm` breakpoint; all
  inputs are large with numeric/decimal keyboards; the submit button is full width; result
  figures use a two-column grid; there are no fixed pixel widths or tables.
- **No redirects.** `/ui` and `/ui/` are both served directly, because a redirect behind the
  ingress proxy would have to rebuild the URL.
- **Data-source badge.** The navbar shows MOCK, RENTCAST or CSV from `get_provider_type()`
  (read on every request), or MISCONFIGURED if `DATA_PROVIDER` is invalid. It never builds a
  provider, so it needs no API key and makes no API call.
- **Theme.** Bootstrap's `data-bs-theme` follows the device's light/dark setting.
- **Status.** The valuation form works. The history page is still a placeholder.
- **Dependencies.** `jinja2` and `python-multipart` (for form posts, in the next step).

## Home Assistant app packaging

The service is packaged as a Home Assistant app so it can be used inside Home Assistant with no
exposed port.

```
repository.yaml                 makes the GitHub repository an app repository
scripts/sync_addon.sh           copies app/ to ha-addon/app/ (--check verifies, changes nothing)
ha-addon/
  config.yaml                   ingress, sidebar entry, options and schema
  Dockerfile                    pinned base-python image, venv, pinned requirements
  run.sh                        thin launcher (bashio shebang), execs entrypoint.py
  entrypoint.py                 options.json -> environment, validation, then uvicorn
  log_config.json               makes the app's own INFO logs visible in the Log tab
  requirements.txt              exact pins of the runtime dependency tree (no test tools)
  translations/en.yaml, DOCS.md, README.md, CHANGELOG.md, icon.png, logo.png, .dockerignore
  app/                          generated copy of ../app (committed; checked by tests)
```

- **Delivery.** An app is built from its own folder, which cannot reach `../app`, so the
  package contains a copy. `sync_addon.sh` keeps it in step, and `tests/test_addon_package.py`
  fails on any difference. `build.yaml` is deprecated and not used: the Dockerfile names its
  base image (`ghcr.io/home-assistant/base-python:3.12-alpine3.24-2026.08.0`, matching the
  Python the tests run on).
- **Ingress only.** `ingress: true`, `ingress_port: 8099`, `ingress_entry: ui/` (the sidebar
  opens the UI; the JSON root `/` is untouched and serves the watchdog), `panel_admin: true`.
  No `ports`, no host network, no host folder mapped, and none of the privilege or API flags.
- **Supervisor-only peer check.** `app/security.py` (`PeerGuardMiddleware`) answers only
  connections whose TCP peer address is in `ALLOWED_PEERS`; the app sets it to the Supervisor,
  `172.30.32.2`. Other apps share the internal network and could otherwise skip ingress and its
  login. It uses the socket peer, never `X-Forwarded-For`, so uvicorn runs with
  `proxy_headers=False`. Missing, unparseable or other peers get `403 Forbidden`. Lists that are
  empty, invalid, written with host bits set (which would widen them) or broader than /16 (/64
  for IPv6) are rejected, and an invalid value stops the app at startup. When the variable is
  unset (development) nothing is checked.
- **Options.** `data_provider` (`mock` default, or `rentcast`), `rentcast_api_key` (password),
  `min_comparables_required` (1-50, default 3), `ui_secret_key` (password, optional),
  `log_level` and `allowed_peers`. `entrypoint.py` validates them (non-text values, unknown
  providers, `rentcast` without a key and unsafe peer lists are refused with a message that
  names the option, never its value), builds the environment and starts uvicorn.
- **Secrets.** The API key travels only through the app's options and the process
  environment. Nothing prints it. The form-security key is generated once into `/data/ui_secret`
  (mode 600) so forms survive restarts, unless one is configured.
- **Persistence.** `DATABASE_PATH=/data/rentpricingtool.db`: a fresh database, with the history
  and the RentCast cache. There is no automatic migration of an earlier database; the optional
  manual steps are in `ha-addon/DOCS.md`. `backup: cold` stops the app while `/data` is copied.
- **Not built or run here.** The Docker image cannot be built inside the development container,
  so the first real build happens when the app is installed. The tests cover the package files,
  the options handling, the bundle and the peer check.

## Geographic modeling

Distance is calculated, not stored.

- `services/geo.py` has two functions. `haversine_distance(lat1, lon1, lat2, lon2)` returns
  the great-circle distance in kilometers, and `miles_between_points(...)` returns it in
  miles. Both take decimal degrees and assume a spherical Earth (mean radius 6371.0088 km),
  which is accurate to about 0.5%.
- `filter_by_distance(comparables, latitude, longitude, max_miles=1.0)` measures from the
  given point to each comparable's coordinates and keeps those within `max_miles`
  (inclusive).
- **The subject's location.** `ValuationRequest` has optional `latitude` and `longitude`.
  They must be given together (one alone is a 422) and are range-checked (+/-90 and
  +/-180). If both are given they are used as they are; otherwise the address is geocoded
  (see Geocoding). `DEFAULT_SUBJECT_LATITUDE` and `DEFAULT_SUBJECT_LONGITUDE` in `config.py`
  (La Mesa, CA) are now only a fallback for code that calls the engine without a
  geocoder; the API always supplies one.
- Distance is straight-line, not driving distance.

The mock data was generated so that each comparable sits at the same distance from the
default subject that the earlier hand-entered `distance_miles` value described. Valuations
for the default point are therefore unchanged from Phase 3. The mock geocoder places
`123 Main St, La Mesa, CA` exactly on that point.

## Geocoding

Addresses are turned into coordinates through a provider interface, in the same style as
the comparable data sources. Two geocoders exist: the mock (13 fictional addresses, no network;
the default in development and for the tests) and the US Census geocoder (the default in the
Home Assistant app).

```
Valuation Engine
      │
      ▼
Geocoder                 (interface: geocode(address) -> {"latitude", "longitude"})
      │
      ├── MockGeocoder                 GEOCODER=mock (default): fixed table, no network
      └── CachingGeocoder              GEOCODER=census
              └── CensusGeocoder       US Census service over HTTPS, injectable transport
```

- **`Geocoder`** (`geocoding/base.py`) has one abstract method, `geocode(address)`. It returns
  a `Coordinates` dict (`latitude`, `longitude`) or raises **`AddressNotFoundError`**.
- **`AddressNotFoundError`** is raised for an unknown address, an ambiguous one, or a blank
  one. Its message says what to do: check the address, or supply latitude and longitude.
- **When it is used.** In `run_valuation()`: if the request has coordinates they are used
  and the geocoder is **not called**; otherwise the address is geocoded. The router injects
  the geocoder through `get_geocoder()`, the one place that names a provider. Failures
  happen before anything is saved to the database.
- **`MockGeocoder`** (`geocoding/mock_geocoder.py`) looks addresses up in a table of 13
  fictional addresses around La Mesa and San Diego (7 in La Mesa, 6 in San Diego), including
  `123 Main St, La Mesa, CA`, `456 Palm Ave, La Mesa, CA` and `789 Broadway, San Diego, CA`.
  Matching ignores case, spacing, periods and "California" versus "CA". A shorter address
  matches when it is the start of exactly one known address: `123 Main St` and
  `123 Main St, La Mesa` both find `123 Main St, La Mesa, CA`. A street that exists in two
  cities (`100 University Ave`) is ambiguous and raises the error; adding the city resolves
  it.
- **Mock addresses and the mock comparables.** The comparables are clustered in La Mesa, so
  the La Mesa addresses can be valued, with different results depending on where they are.
  The San Diego addresses are 3 to 14 miles away, outside the 1-mile search radius, so they
  geocode fine but valuing them returns 404 "no comparable properties". That is the mock
  data's limit, not a geocoding problem.
- **Errors over the API.** An address that can't be geocoded returns **404** with the
  geocoder's message; a successful geocode that leaves too few comparables also returns 404,
  with the insufficient-data body described under Minimum comparables.
- **Stored history.** The database keeps the latitude and longitude *as the request gave
  them*, so they are NULL for a geocoded request; the geocoded point is not stored.

### Census geocoder and the geocode cache

`GEOCODER` selects the geocoder (`mock`, the default, or `census`), read on every call by
`geocoder_config.get_geocoder_type()`; `geocoder_registry.build_geocoder()` builds it, and
`get_geocoder()` in the router delegates to it, so tests still override that dependency.
Building a geocoder makes no network call.

- **`CensusGeocoder`** makes one GET to the Census one-line-address service
  (`geocoding.geo.census.gov`, benchmark `Public_AR_Current`, JSON). The service is free, needs
  no account, and covers US addresses only. It places an address on its street by interpolating
  the street's address range: accurate to tens of metres, not a rooftop, which is plenty for the
  engine's 1-mile comparable radius. A match has `coordinates.x` = longitude and `.y` =
  latitude (a test pins that order, and out-of-range values are refused). No match raises
  `AddressNotFoundError`; several matches more than 0.5 miles apart are ambiguous (the same
  street in two towns) and ask for the city, state and ZIP code. The HTTP call is an injectable
  `transport`, so tests make no network calls.
- **Cleaning.** `geocoding/normalize.py` removes `Apt`, `Unit`, `Suite`/`Ste` and `#number`
  designators before the lookup (a street such as "Unit St" is kept), and collapses spacing.
  The same normalization, plus case, periods and "California" vs "CA", forms the cache key, so
  `123 Main St Apt 4B` and `123 MAIN ST #7` share one entry.
- **Errors.** Failures of the service are `GeocoderUnavailableError` (HTTP 429, 5xx, timeout,
  network failure, or an HTML maintenance page; 503) and `GeocoderResponseError` (anything else
  unexpected; 502). Both subclass `DataSourceError`, so the existing handlers and the UI show a
  generic message ("The address lookup service is unavailable; try again shortly, or enter exact
  coordinates") and never the URL or the exception text. They are not "address not found": the
  address may be fine. Transient failures are retried once with backoff
  (`GEOCODER_MAX_RETRIES`, `GEOCODER_RETRY_DELAY_SECONDS`, `GEOCODER_TIMEOUT_SECONDS`).
- **`CachingGeocoder`** remembers answers in the existing `provider_cache` table under keys
  starting `geocode` (no schema change): found addresses for 90 days
  (`GEOCODE_CACHE_TTL_SECONDS`, 0 turns the cache off) and "not found" for one day
  (`GEOCODE_NOT_FOUND_TTL_SECONDS`). Service failures are never cached. It keeps already-seen
  addresses working during an outage and keeps load on the free service low. Like the RentCast
  cache it is best effort: a database problem or a damaged entry just means a lookup.
- **Order of events.** The engine locates the subject *before* fetching comparables, so a failed
  address lookup never reaches the comparable data source, and RentCast is not called. Only a
  successful lookup leads to a RentCast request.
- **Startup.** The app logs `Geocoder: census` and `Geocode cache: enabled` (or `mock` and
  `disabled`), and warns `RentCast provider active with mock geocoder` when real listings would
  be paired with the demo address table. A bad setting is logged and the app keeps running.
- **Logging.** Each lookup logs only its outcome and timing (`geocode source=census result=match
  matches=1 ms=...`, `geocode result=match cache=hit`), never the address.
- **Form.** With the mock the form says address lookup is in demo mode; with Census it asks for
  the city, state and ZIP code.

### RentCast provider

`RentCastComparableSource` (`DATA_PROVIDER=rentcast`, `RENTCAST_API_KEY` required) makes one
`GET /v1/listings/rental/long-term` call per uncached area, searching by the subject's
coordinates and a radius. Listings missing any field a `Comparable` needs are skipped (and
counted in a log warning). Results are cached in the application's SQLite database
(`provider_cache` table, `persistence/cache.py`) by rounded coordinates, radius and limit, so
they survive restarts: the router builds a new source per request and every call is
billable. The cache is best effort; if the database is unavailable it reads as empty and the
API is called normally. Entries expire after `RENTCAST_CACHE_TTL_SECONDS` and expired rows
are purged on the next write. The HTTP call is an
injectable `transport`, so tests make no network calls.

Failures raise `DataSourceError` subclasses, each carrying the HTTP status and a generic
public message (the exception text is only logged, never returned to the client):

| Error | When | API response |
|---|---|---|
| `RentCastAuthError` | HTTP 401/403 | 502 |
| `RentCastRateLimitError` | HTTP 429 | 503 |
| `RentCastUnavailableError` | HTTP 5xx, timeout, network failure | 503 |
| `RentCastResponseError` | other statuses, invalid JSON | 502 |
| `ProviderConfigurationError` | missing key, bad setting, unknown `DATA_PROVIDER` | 503 |

`main.py` registers handlers for these, so they also cover errors raised while the router
dependency builds the source. Transient failures (429, 5xx, network) are retried with
doubling backoff (`RENTCAST_MAX_RETRIES`, default 1; `RENTCAST_RETRY_DELAY_SECONDS`, default
0.5); auth and response errors are not. At startup the app logs the chosen provider, or logs
an error if it is misconfigured, and keeps running so `/history` and `/stats` still work.

### Future providers

| Provider | Stub | Notes |
|---|---|---|
| Nominatim | | Keyless fallback for addresses Census cannot place. The public service allows 1 request/s, needs an identifying User-Agent and requires results to be cached. |
| Geocodio | | Rooftop-accurate US/Canada geocoder with a free tier (2,500 lookups/day), needs a key; the upgrade path if Census proves unreliable. |
| Google | `geocoding/future_google.py` | Needs a billing account and key; latitude/longitude may be cached for only 30 days and not used with a non-Google map. |
| Other | | Mapbox (its temporary geocoding forbids caching), a chain of providers: anything that implements `Geocoder`. |

To add one: subclass `Geocoder`, add it to `GeocoderType` and `geocoder_registry.build_geocoder()`,
raise `GeocoderUnavailableError`/`GeocoderResponseError` for service failures (never
`AddressNotFoundError`), and test it with a stubbed HTTP layer.

## Persistence

Valuation history is kept in a single SQLite file using Python's built-in `sqlite3`
(no ORM, no migrations tool).

- **Location.** `data/rentpricingtool.db` in the project, or the path in the
  `DATABASE_PATH` environment variable. The file, its folder and the schema are created
  automatically: at application startup, and again on first use if needed. The `data/`
  folder's contents are git-ignored.
- **`DatabaseManager`** (`persistence/database.py`) opens connections and creates the schema
  with `CREATE TABLE IF NOT EXISTS`. Each call to `connect()` returns a new connection,
  because sqlite3 connections must not be shared between threads and FastAPI runs sync
  endpoints in a thread pool. `connection()` is a context manager that commits on success,
  rolls back on error, and always closes.
- **Schema.** Two tables. `provider_cache` (`cache_key`, `value` as JSON, `created_at`,
  `expires_at`; see the RentCast provider) holds paid-API responses. `valuation_requests`: `id`, `created_at` (ISO 8601 UTC text), the
  request (`address`, `beds`, `baths`, `sqft`, `latitude`, `longitude`) and the result
  (`comparable_count`, `p25`, `median`, `p75`, `average`, `recommended_rent`). The
  `confidence` and `funnel` values are returned but not stored.
  `latitude` and `longitude` hold what the request supplied, so they are NULL when the
  default subject point was used.
- **`ValuationRepository`** (`persistence/repositories.py`) is the only code that contains
  SQL, always with bound parameters:
  `save_valuation_request()`, `get_recent_valuations(limit=25)` (newest first) and
  `count_valuations()`, plus `database_path` and `database_size_bytes()` for `/stats`.
  Database errors surface as `PersistenceError`.
- **When results are saved.** `run_valuation()` saves each *successful* valuation when it is
  given a repository; the router injects one. Valuations with insufficient data are not
  saved. Saving is best effort: if the database is unavailable the error is logged and the
  valuation is still returned, so a database problem never breaks pricing. `/stats` and
  `/history` answer 503 in that case.
- **Tests** never touch the real database: `tests/conftest.py` overrides the repository
  dependency with a temporary one for every test.

## Current Data Flow

```
Valuation Engine
      │
      ▼
ComparableDataSource      (interface: get_comparables(subject=None) -> list[Comparable])
      │
      ▼
MockComparableSource      (static, in-memory, 26 records)
```

The engine asks its `ComparableDataSource` for comparables and never learns where they
came from. `run_valuation(subject, source)` receives the source as an argument
(dependency injection). The router supplies it through a FastAPI dependency,
`get_comparable_source()`, which is the single place that names a concrete provider.
Tests can inject a fake source, or override that dependency to exercise the API with their
own data, with no change to the engine.

`get_comparables(subject=None)` receives the subject as a `SubjectProperty` (address,
resolved latitude and longitude, beds, baths, sqft). A source that searches by location can
use it to narrow its query; a fixed dataset such as the mock ignores it, and every source
must still work when it is `None`. It returns the unfiltered set. Filtering (`comparables.py`), outlier
removal and statistics happen in the engine, so every provider is treated the same way.

### Adding a provider

1. Subclass `ComparableDataSource` and implement `get_comparables(self, subject=None)`, returning
   `Comparable` dicts.
2. Return the provider from `get_comparable_source()` in the router (later this can be
   chosen from configuration).
3. Add tests that stub any I/O. The suite makes no network calls.

### Future providers

Two stub modules mark where the next providers go. They contain TODO notes only.

| Provider | Stub | Notes |
|---|---|---|
| RentCast | `data_sources/rentcast_source.py` | Fetch listings from the RentCast API and map them to `Comparable`. Needs an API key from the environment. Listings carry coordinates, so they map straight to `latitude` and `longitude`. |
| CSV | `data_sources/csv_source.py` | Load comparables from a CSV file (for example under `data/`), validating each row. |
| Other | | Anything that can produce `Comparable` records: a database, another listings API, or a combination of providers. |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check: `{"app": "Rent Pricing Tool", "status": "online"}` |
| POST | `/valuation` | Valuation for a subject property |
| GET | `/stats` | Valuation count and database details |
| GET | `/history` | The latest 25 valuations, newest first |
| GET | `/docs` | Swagger UI (generated by FastAPI) |

`POST /valuation` responses:

| Status | When |
|---|---|
| 200 | A valuation was produced |
| 404 | The address could not be geocoded (and no coordinates were given), or fewer than the minimum number of comparables remained (an `insufficient_data` body, below). The `detail` message says which. |
| 422 | The request body is missing fields or has the wrong types (FastAPI/Pydantic) |

### Minimum comparables

A valuation built on one or two listings is not a meaningful statistic, so the engine
refuses to produce one. After the filters **and** outlier removal, if fewer than
`MIN_COMPARABLES_REQUIRED` comparables remain (default **3**, set from the environment, at
least 1), `run_valuation()` raises `InsufficientDataError` and the API answers 404 with:

```json
{
  "status": "insufficient_data",
  "detail": "Only 2 comparable properties remained after filtering; at least 3 are required for a valuation.",
  "comparable_count": 2,
  "minimum_required": 3,
  "funnel": { "comparables_fetched": 12, "comparables_after_distance_filter": 4, "comparables_after_attribute_filter": 2, "comparables_after_outlier_filter": 2, "comparables_used": 2 }
}
```

`comparable_count` can be 0, in which case `detail` is "No comparable properties found for
this property." No valuation fields are present, and the result is not saved. A successful
valuation has no `status` field. The status stays 404 (as "no comparables" always was) so
existing clients keep working; clients should read `status` to tell the cases apart.
`InsufficientDataError` subclasses `NoComparablesError`. `run_valuation(...,
min_comparables=n)` overrides the default per call, which tests use.

`ValuationResponse` also carries `confidence` and `funnel` (see Quality diagnostics).

`GET /stats` returns `{"total_valuations": 123, "database_path": "...", "database_size_kb": 42}`.
`GET /history` returns a list of the stored rows, each with `id`, `created_at`, the request
fields, and the result fields. Both answer 503 if the database cannot be read. There is no
authentication, so these endpoints expose stored addresses and the server's file path to
anyone who can reach the service.

## Configuration

`config.py` reads four settings from environment variables, each with a default (it also
holds the default subject coordinate, a plain constant):

| Variable | Default |
|---|---|
| `APP_NAME` | `Rent Pricing Tool` |
| `VERSION` | `0.2.0` |
| `ENVIRONMENT` | `development` |
| `DATABASE_PATH` | `data/rentpricingtool.db` in the project |

The app does not load `.env` by itself. Use `uvicorn app.main:app --env-file .env`.

## Testing

`pytest` runs the whole suite with no network access:

- `test_statistics.py`: percentiles, average, standard deviation, outlier removal
- `test_geo.py`: haversine and miles, with a zero-distance case, known city-pair distances,
  symmetry and edge cases
- `test_comparables.py`: mock dataset sanity and each filter, including boundaries and
  coordinate-based distance filtering
- `test_data_sources.py`: the interface, the mock source, and injecting a fake source into
  the engine and the API
- `test_valuation_engine.py`: result shape, `recommended_rent == median`, outlier handling,
  determinism, the no-comparables error, and saving to a repository
- `test_database.py`, `test_repositories.py`: schema, automatic creation, transactions,
  saving and reading rows, SQL-injection safety, error handling
- `test_persistence_api.py`: `/stats` and `/history`, saving through the API, the 503 paths
- `test_geocoding.py`: the interface, the mock geocoder (known, unknown, ambiguous and blank
  addresses, no network use), coordinates bypassing the geocoder, and `/valuation` with an
  address only, with coordinates only, and with an unknown address
- `test_minimum_comparables.py`, `test_quality.py`: the minimum-comparables rule, confidence
  levels, funnel counts, the funnel log line, and diagnostics in the insufficient-data body
- `test_peer_guard.py`: the Supervisor-only peer check (parsing, refusals, spoofed headers,
  WebSockets, the real app behind it, and how `main.py` wires it)
- `test_addon_package.py`: the Home Assistant app package (settings, ingress, no published
  ports, pinned image and requirements, the bundle matching `app/`, `sync_addon.sh`, the
  entrypoint's option handling and secrets, and the docs)
- `test_ui_forms.py`, `test_ui_valuation.py`: form validation, CSRF, view models, the
  submit flow and every page state (result, insufficient data, field errors, provider errors),
  escaping, error pages, ingress and mobile layout assertions. They use mock sources and
  dependency overrides, and fail if anything tries to use the network
- `test_ui_infrastructure.py`: UI pages and templates, the data-source badge, local Bootstrap
  assets and their checksums, no external URLs, static-file path safety, ingress prefix
  handling (including unsafe header values), and that the JSON API and OpenAPI schema are
  unchanged
- `test_api.py`: endpoints through FastAPI's `TestClient`

Because the data and the algorithm are deterministic, tests assert exact values.

## Design decisions

- **Pure functions over classes.** The services are plain functions that take data and
  return data, which keeps them easy to test and to swap out.
- **Data behind an interface.** The engine receives a `ComparableDataSource` and never
  touches a dataset directly, so a real data source can replace the mock without changing
  the engine. The source is injected, not constructed inside the engine.
- **Errors as exceptions in the domain, status codes at the edge.** The engine raises
  `NoComparablesError` (or its subclass `InsufficientDataError`); only the router decides
  that means HTTP 404.
- **No new dependencies for the math.** Statistics are implemented with the standard
  library so results are reproducible and the dependency list stays small.

## Known limitations

- The default data source is static mock data (illustrative only); live RentCast data is chosen
  with `DATA_PROVIDER`.
- With `GEOCODER=mock` (the development default) geocoding knows only 13 fixed addresses; any
  other address needs coordinates in the request. The Census geocoder (the Home Assistant app
  default, `GEOCODER=census`) covers US addresses only, places them by street-range
  interpolation rather than at the rooftop, and has no service guarantee (answers are cached to
  soften outages). The San Diego mock addresses are outside the mock comparables, so valuing
  them with mock data returns 404.
- Addresses typed without a city, state or ZIP code can be ambiguous with a real geocoder; the
  form asks for all three.
- Distance is straight-line (great-circle), not driving distance.
- Confidence depends only on the number of comparables, not on how widely their rents vary.
- No authentication or rate limiting. `/history` and `/stats` are open.
- History is never pruned and the schema has no migration tooling; changing the table means
  handling existing database files by hand.
- One SQLite file suits a single-machine deployment, not several instances sharing data.
- Logging is plain text (valuation funnel and geocoding lines); there are no metrics.

## Planned (from the README roadmap)

- RentCast and CSV providers (stubbed in `data_sources/`) to replace the mock comparables
- A real geocoding provider (Google, stubbed in `geocoding/`), so any address works
