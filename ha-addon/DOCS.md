# Rent Pricing

## Install

1. In Home Assistant open **Settings > Apps > App store**.
2. Open the three-dot menu (top right) and choose **Repositories**.
3. Add `https://github.com/cskullerud/rentpricingtool` and close the dialog.
4. Reload the page. **Rent Pricing** appears under the **Rent Pricing Tool** repository.
5. Open it and choose **Install**. The first install builds the app on your Home Assistant, which
   downloads Python packages and can take a few minutes. It needs internet access.

## First run

1. On the app's **Configuration** tab leave everything at its defaults. The data source is
   `mock` (built-in sample data) and the address lookup is `census` (free), so nothing is charged.
2. On the **Info** tab turn on **Show in sidebar**, then choose **Start**.
3. Open the **Log** tab. You should see `Rent Pricing: data source mock, address lookup census ...`,
   the lines `Geocoder: census` and `Geocode cache: enabled`, and `Uvicorn running on
   http://0.0.0.0:8099`. If you see `ERROR:` instead, the message names the option to fix.
4. Click **Rent Pricing** in the sidebar. The page opens on the valuation form, with a grey
   **MOCK** badge at the top right.
5. The sample comparables are all in La Mesa, CA. To see a result with sample data, open
   **Use exact coordinates** and enter latitude `32.7678`, longitude `-117.0231`, with any
   address, 3 bedrooms, 2 bathrooms and 1400 sq ft. The result is $2,512 / month with high
   confidence from 16 comparables. (An address elsewhere is looked up fine but finds no sample
   comparables, so the page says there is not enough data.)
6. Each valuation also writes one `valuation_funnel ...` line to the Log tab.

Only administrators see the sidebar entry. The page works the same in the Home Assistant mobile
app and over remote access, because it is served through Home Assistant itself.

## Configuration

| Option | Meaning |
|---|---|
| Data source (`data_provider`) | `mock` (default) or `rentcast`. |
| Address lookup (`geocoder`) | `census` (default): the free US Census geocoder. `mock`: a few demo addresses only. |
| RentCast API key (`rentcast_api_key`) | Required for `rentcast`. Stored masked by Home Assistant. |
| Minimum comparables (`min_comparables_required`) | 1 to 50, default 3. Below it the page says there is not enough data. |
| Form security key (`ui_secret_key`) | Optional. If empty, a key is created once and kept in the app's storage. |
| Log level (`log_level`) | `debug`, `info` (default), `warning` or `error`. |
| Allowed source addresses (`allowed_peers`) | Advanced. Default `172.30.32.2`, the Supervisor. |

Save the options and **restart** the app for changes to take effect.

### Search controls

The form has three controls beside the property details. They are what the valuation searches by,
and the **Search criteria** panel above the Get valuation button always shows the current choices
and every rule behind them (nothing is hidden).

| Control | Choices | Default |
|---|---|---|
| Search radius (miles) | 0.5, 1, 2, 3, 5 miles | 1 mile |
| Lookback window (days) | 30, 90, 180, 365 days | 90 days |
| Building type | All, Home (single-family), Condo, Apartment | All |

- **Search radius.** RentCast is asked for listings within this radius, and listings farther away
  are dropped. A larger radius finds more listings but they are less alike. If a search finds too
  few, the page tells you how far away the nearest listing is.
- **Lookback window.** Listings that have been on the market longer than this are ignored (a listing
  with no known age is kept). It is applied to the listings already fetched, so changing it never
  costs another RentCast request. It does not apply to the built-in sample data.
- **Building type.** Sent to RentCast as its property-type filter ("Home" is RentCast's "Single
  Family"). Townhouse, manufactured and multi-family homes are included only with "All".
- **Square feet are optional.** Leave them blank and listings are not matched on size, and the
  confidence is lowered one level (and the result says so).
- **Matching rules** (fixed): bedrooms within 1, bathrooms within 1, size within 20%. Rents that
  are unusually high or low are removed when there are at least 4 comparables. At least 3
  comparables are needed for a valuation.
- **Result size.** RentCast returns up to 500 listings, most recently seen first. If the Log tab
  says `RentCast response reached limit (500)`, nearby listings may be missing: choose a smaller
  radius.

### Address lookup

With `census`, type a US address with its city, state and ZIP code, for example
`123 Main St, San Diego, CA 92101`, and the app finds its location itself (you can still enter
latitude and longitude to skip the lookup).

- It is free and needs no account or key. A lookup **never uses a RentCast request.**
- Apartment, unit, suite and `#number` designators are ignored.
- Found addresses are remembered for 90 days in the app's database (an unknown address for one
  day), so repeating an address makes no lookup, and already-seen addresses keep working if the
  service is down.
- It places an address on its street (street-range interpolation, not the rooftop): accurate to
  tens of metres, which is plenty for finding comparables within a mile.
- The Census service has no service guarantee. If it is down or busy the page says the address
  lookup is unavailable (not "address not found"); try again shortly, or enter coordinates.
- US addresses only. An address without a city, state or ZIP code may be ambiguous; the page
  then asks for them.

### Checking the address lookup

1. After updating to 0.2.0, the Log tab shows `Geocoder: census` and `Geocode cache: enabled`.
2. With the data source still on `mock`, enter a real address with its ZIP code and leave the
   coordinates empty. The lookup works if the page says **Not enough comparable properties**
   with a funnel starting at 26 fetched and 0 within 1 mile (the sample comparables are far
   away). The Log tab shows `geocode source=census result=match`.
3. Submit the same address again: the Log tab shows `geocode result=match cache=hit`.
4. An address that does not exist shows "Address not found" on the address field and opens
   the coordinates section.
5. Only then switch the data source to `rentcast` (each new area uses one RentCast request) and
   value an address near the one you tried.

### Using live RentCast data

1. Get an API key from RentCast and make sure your plan is active.
2. Set **Data source** to `rentcast`, paste the key into **RentCast API key**, save, and restart.
   If the key is missing the app refuses to start and says why in the Log tab. Keep **Address
   lookup** on `census`: with `mock` real addresses are not found, and the Log tab warns about it.
3. The badge at the top right now reads **RENTCAST**, and a note under the button says live data
   is on.

**Cost.** Each valuation for a new area (a new location, radius or building type) uses one request from your RentCast plan. Results are
cached for 24 hours in the app's database, so repeating the same area is free, even after a
restart. Nothing calls RentCast until you submit the form, and the default is `mock`.

## Security

- **Ingress only.** The app publishes no ports and does not use the host network. The only way in
  is Home Assistant, behind its login.
- **Supervisor-only.** Other apps share Home Assistant's internal network, so the app also
  answers only connections from the Supervisor (`allowed_peers`). A direct request from anything
  else gets `403 Forbidden` and a warning in the Log tab.
- **Secrets.** The API key lives in the app's options (shown masked, never written to the Log tab).
  It is included in Home Assistant backups, so **encrypt your backups**. The form-security key is
  generated and stored in the app's private storage.
- **Forms** carry a token tied to your browser, so another web page cannot make your browser run
  valuations.

## Backups

The app's storage (`/data`) is included in Home Assistant backups. The app is stopped briefly
while a backup is taken so the database is never copied mid-write.

## Data and migration

The app starts with a **fresh** database at `/data/rentpricingtool.db`. Nothing is migrated
automatically.

*Optional, advanced and untested on your system:* to carry over an existing database, stop the
app, make a consistent copy (for example `sqlite3 old.db ".backup new.db"`), put it in the app's
data folder on the host as `rentpricingtool.db` (on Home Assistant OS this is under
`/mnt/data/supervisor/addons/data/`, in the folder whose name ends in `_rentpricingtool`;
reaching it needs the SSH app with protection mode off, or the host console), and start the app
again. If you start without migrating you lose nothing but the old valuation history.

## Updating

When a new version is published, **Update** appears on the app's page. Updating rebuilds the
app (internet access needed). Your database and settings are kept.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Sidebar entry missing | Turn on **Show in sidebar** on the Info tab. Only administrators see it. |
| `403 Forbidden` in the page | The Log tab shows `Refused a connection from <address>`. If that address is where ingress traffic really comes from, put it in **Allowed source addresses**. |
| The app stops with `ERROR: ...` | Read the message in the Log tab; it names the option to fix. |
| Page says the data source is unavailable | The data service could not be reached or rejected the key (see the Log tab). Check your RentCast plan and key. |
| "The address lookup service is unavailable" | The Census service is down or busy. Try again shortly or enter coordinates. Addresses looked up before still work. |
| "Address not found" | Check the spelling and include city, state and ZIP code, or enter coordinates. |
| "Not enough comparable properties" | Too few listings matched. The page shows where they were lost. Exact coordinates can help if the address was placed in the wrong spot. |
| Install fails | Check the Supervisor log under **Settings > System > Logs**. The build needs internet access. |
