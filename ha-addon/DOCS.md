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
   `mock` (built-in sample data), so nothing is charged.
2. On the **Info** tab turn on **Show in sidebar**, then choose **Start**.
3. Open the **Log** tab. You should see `Rent Pricing: data source mock ...` and, further down,
   `Uvicorn running on http://0.0.0.0:8099`. If you see `ERROR:` instead, the message names the
   option to fix.
4. Click **Rent Pricing** in the sidebar. The page opens on the valuation form, with a grey
   **MOCK** badge at the top right.
5. Try `123 Main St`, 3 bedrooms, 2 bathrooms, 1400 sq ft. With sample data the result is
   $2,512 / month with high confidence from 16 comparables.
6. Each valuation also writes one `valuation_funnel ...` line to the Log tab.

Only administrators see the sidebar entry. The page works the same in the Home Assistant mobile
app and over remote access, because it is served through Home Assistant itself.

## Configuration

| Option | Meaning |
|---|---|
| Data source (`data_provider`) | `mock` (default) or `rentcast`. |
| RentCast API key (`rentcast_api_key`) | Required for `rentcast`. Stored masked by Home Assistant. |
| Minimum comparables (`min_comparables_required`) | 1 to 50, default 3. Below it the page says there is not enough data. |
| Form security key (`ui_secret_key`) | Optional. If empty, a key is created once and kept in the app's storage. |
| Log level (`log_level`) | `debug`, `info` (default), `warning` or `error`. |
| Allowed source addresses (`allowed_peers`) | Advanced. Default `172.30.32.2`, the Supervisor. |

Save the options and **restart** the app for changes to take effect.

### Using live RentCast data

1. Get an API key from RentCast and make sure your plan is active.
2. Set **Data source** to `rentcast`, paste the key into **RentCast API key**, save, and restart.
   If the key is missing the app refuses to start and says why in the Log tab.
3. The badge at the top right now reads **RENTCAST**, and a note under the button says live data
   is on.

**Cost.** Each valuation for a new area uses one request from your RentCast plan. Results are
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
| "Not enough comparable properties" | Too few listings matched. The page shows where they were lost. Exact coordinates can help if the address was placed in the wrong spot. |
| Install fails | Check the Supervisor log under **Settings > System > Logs**. The build needs internet access. |
