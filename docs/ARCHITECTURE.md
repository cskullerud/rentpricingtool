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
    ingress.py         IngressMiddleware: X-Ingress-Path -> ASGI root_path
    forms.py           ValuationForm: parsing, per-field messages, keeps what was typed
    csrf.py            cookie-bound CSRF tokens
    viewmodels.py      money(), confidence styles, funnel rows, the insufficient-data explanation
    errors.py          HTML error pages (404, 405, provider problems), is_ui_page_request()
  templates/           base.html, valuation_form.html, history.html, error.html
    partials/          _form_fields, _result_card, _confidence_badge, _funnel, _insufficient, _alert
  static/              mounted at /ui/static: vendor/bootstrap, css/app.css, js/app.js
```

- **Ingress-safe URLs.** Home Assistant ingress serves the app under a prefix and reports it in
  `X-Ingress-Path`. The middleware copies a *validated* value into `root_path` (single leading
  `/`, only letters, digits and `_ . - /`, no `..`, at most 200 characters; anything else is
  ignored), and templates build every link, form action and static URL with `{{ u('/ui/...') }}`.
  Without the header the prefix is empty. The JSON API is not affected.
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
the comparable data sources. Only a mock exists; nothing calls an external service.

```
Valuation Engine
      │
      ▼
Geocoder                (interface: geocode(address) -> {"latitude", "longitude"})
      │
      ▼
MockGeocoder            (fixed table of 13 addresses, no network)
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
| Google | `geocoding/future_google.py` | Call the Google Geocoding API from `geocode()`. Needs an API key from the environment, handling of quota and network errors (not "address not found"), refusal of imprecise matches, and caching. |
| Other | | Mapbox, Nominatim, a local geocoder, or a chain of providers: anything that implements `Geocoder`. |

To add one: subclass `Geocoder`, return it from `get_geocoder()` in the router (later this
can be chosen from configuration), and test it with a stubbed HTTP layer.

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
| `VERSION` | `0.1.0` |
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

- The only data source is static mock data, so valuations are illustrative only.
- The data source is chosen in code (`get_comparable_source()`), not from configuration.
- Geocoding is a mock with 13 fixed addresses; any other address needs coordinates in the
  request. The San Diego mock addresses are outside the mock comparables, so valuing them
  returns 404.
- The geocoder is chosen in code (`get_geocoder()`), not from configuration.
- Distance is straight-line (great-circle), not driving distance.
- Confidence depends only on the number of comparables, not on how widely their rents vary.
- No authentication or rate limiting. `/history` and `/stats` are open.
- History is never pruned and the schema has no migration tooling; changing the table means
  handling existing database files by hand.
- One SQLite file suits a single-machine deployment, not several instances sharing data.
- No logging or metrics.

## Planned (from the README roadmap)

- RentCast and CSV providers (stubbed in `data_sources/`) to replace the mock comparables
- A real geocoding provider (Google, stubbed in `geocoding/`), so any address works
