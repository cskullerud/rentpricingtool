# Rent Pricing

Rent valuations from comparable listings, as a page in the Home Assistant sidebar.

Enter an address, bedrooms, bathrooms and square footage. The app finds comparable
properties, removes outliers, and shows a recommended rent, a confidence level and how the
comparables were narrowed down. It starts on built-in sample data, so it costs nothing until
you choose to switch to live RentCast data.

- Looks addresses up itself with the free US Census geocoder (no key), and remembers them.
- Opens from the **Rent Pricing** sidebar entry (administrators), in the web app, the mobile
  app and over remote access.
- No ports are published. The page is reachable only through Home Assistant.
- Keeps its history and cache in the app's own storage, included in backups.

See the **Documentation** tab for installation, the first-run checklist and settings.
