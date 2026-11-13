# Per-entry location and time (PoC item 24) — design

Status: draft for Andrew's review, 2026-11-12.
Replaces the May scaffold notes for item 24 in `POC_PHASE2_TODO.md`.

## Why

A diary entry happens somewhere. Today the app has no idea where:

- **Weather follows the wrong place, or none.** The weather badge uses the first photo with GPS, else the diary's home location. No diary has a home location, because the web app never offered a way to set one (0 of 16 local diaries). So a day without a GPS photo gets no weather, and a day trip gets the weather of wherever a photo says, or nothing.
- **Calendar events already say where.** Google Calendar events carry location text ("Mukilteo Library, 4675 Harbour Pointe Blvd, Mukilteo, WA"), and the app ignores it.
- **Times can be wrong on trips.** An entry's date is worked out in the diary's time zone, and event times are shown in the viewer's browser time zone. A 9 am event in Tokyo shows as 5 pm the day before for a parent in Los Angeles.

Product principle: it should just work for busy parents. Location fills itself in; the parent only steps in to correct it.

## Goals

1. Every entry has its own place: a display name (for example "Mukilteo, Washington") plus coordinates.
2. The place fills itself in where the app can tell, and never guesses wrong.
3. The parent can set or correct an entry's place, and the diary's home place, with a city search.
4. Weather uses the entry's place.
5. An advanced, hidden-by-default per-entry time zone that shows the entry's event times in that zone and moves the entry's date to match.
6. The stored place is ready for the later map view (backlog P-6).

## Non-goals

- The map view itself (P-6).
- Passing the place to the AI draft prompt. The event's location text already reaches the prompt as source data; adding a second, derived version risks the AI repeating a guess.
- Places outside the US filling in automatically. The rules below only understand US addresses; elsewhere the parent picks the city.
- Reverse geocoding (turning photo GPS into a place name). Open-Meteo does not offer it.
- Setting the time zone automatically from the place.
- Which time zone event times use when no override is set: unchanged (the viewer's browser zone).
- A stronger geocoding service (see "Geocoder choice").

## Anti-goals

- **A wrong place is worse than no place.** It shows wrong weather and misleads the parent. When unsure, fall back; never guess.
- **No new noise.** A place that could not be found is not an error the parent sees.
- **The parent's choice is never overwritten** by anything automatic.

## Decisions (Andrew, 2026-11-12)

1. Calendar event location text may be sent to an outside geocoding service.
2. The per-entry time zone stays, as an advanced option that is not normally shown. It changes both how the entry's event times are shown and the entry's date.
3. Geocoder: Open-Meteo, with the strict rules below, about half of entries filled automatically and none wrong, accepted for the PoC. A stronger service is noted for later.

## Geocoder choice and test (2026-11-12)

Open-Meteo's geocoding API (`https://geocoding-api.open-meteo.com/v1/search`) is free, needs no key for non-commercial use, and is built on GeoNames. Per its docs it matches **cities, towns and postal codes, not street addresses or venues**, and a search must match the **start** of a place name. Each result carries `latitude`, `longitude`, `timezone`, `admin1` (state), `country_code` and `feature_code`. Licence: CC BY 4.0, which requires a link to Open-Meteo wherever its data is shown. Sources: <https://open-meteo.com/en/docs/geocoding-api>, <https://open-meteo.com/en/licence>.

Tested on the 49 distinct location texts in Andrew's local synced calendar:

| Approach | Found a place | Wrong place |
|---|---|---|
| Text as written | 1 of 49 | — |
| Simple rules (any ZIP, any comma part) | 33 of 49 | at least 10: venue names matched towns in Finland's Åland Islands and Peru, "Washington" matched Washington D.C., a theme park matched Disneyland Paris, house numbers read as ZIP codes landed in New York |
| **Strict rules (below)** | **25 of 49 (51%)** | **0** (every match in the state the text names) |

The 24 left over are bare venue names, "Home", or text with no state. They fall back as described below.

**Later, if needed:** Google's Geocoding API understands venues and street addresses, but needs a billing account and an API key.

## Behaviour

### Where an entry's place comes from

First match wins:

1. **Set by the parent.** Kept until the parent changes it or chooses "Use automatic location".
2. **GPS from a photo on the entry.** The first photo with coordinates. It has no place name (see non-goals); it is shown as "From a photo".
3. **The location text of the entry's calendar events**, turned into a place with the strict rules. Events are tried in time order; the first whose text resolves wins.
4. **The diary's home place**, if set.
5. Otherwise no place, and the entry has no weather, as today.

The source is stored with the place (`parent`, `photo`, `event` or `home`), so the app knows what to show, and knows that a `parent` place must never be replaced.

### Strict rules for calendar text (US only)

- Drop a trailing "USA" or "United States".
- **ZIP:** use a 5-digit ZIP only when it directly follows a US state abbreviation ("WA 98072"), search it as a postal code in the US, and accept a result only if it is in that state.
- **City:** otherwise, the text must end in a recognised state (abbreviation such as "WA", or full name such as "Washington", optionally followed by a ZIP). The part just before it is the city, but never the first part (that is the venue), and never a part containing digits. Search it in the US and accept only a populated place (`feature_code` starting `PPL`) in that state.
- Anything else: no match. No guessing.
- Results are cached, so each location string is looked up once. A definite "no match" is cached too. A lookup that fails (network, error) is not cached and is tried again next time.

### When the place is worked out

- When an entry's draft is generated (the step that already fetches weather), before the weather is fetched.
- When a photo is added to or removed from an entry, or an event is attached.
- When the parent sets the place or chooses "Use automatic location".
- When a diary's home place changes, for entries whose place came from `home` or that have none.
- If the place changes, the entry's stored weather is discarded and fetched again for the new place. Changes made through the API queue this on the worker, so the request returns at once.

### What the parent sees

- On the entry: the place next to the weather, for example "Mukilteo, Washington · 54°F rain", with a small "Location and weather: Open-Meteo" link (the licence requires it; the weather badge has been missing it).
- An "Edit location" control on the entry: a city search box showing matches as "City, State, Country"; choosing one sets a `parent` place. "Use automatic location" clears it.
- Diary settings: a "Home location" city search, the same box.
- A viewer sees the place on any entry they can see. It shows only the town, never the event's address.

### Per-entry time zone (advanced)

- Hidden under "Advanced" on the entry, off by default. A list of time zones (the browser's own list) with "Diary time zone" as the default.
- When set:
  - The entry's event times are shown in that zone, labelled with it ("9:00 AM JST").
  - If the entry has calendar events, its date (and end date, for a multi-day entry) is worked out again from those events in that zone. An entry without events keeps its date.
  - Weather follows the new date.
- Clearing it goes back to the diary's time zone and works the date out again the same way.
- The parent can still edit the date by hand afterwards; a later change of time zone works it out again.
- It does not change when an event counts as over (that uses absolute times), and it does not change the weather lookup, which already uses the place's own time zone.

## Data and API

- `entries`: `location_name` (text, nullable), `lat`, `lon` (numeric, nullable), `location_source` (`parent | photo | event | home`, nullable), `tz_override` (IANA name, nullable). Alembic migration `0012`, with downgrade.
- `diaries`: `location_name` (text, nullable). `lat`/`lon` already exist.
- `EntryOut` gains `location` (`{name, lat, lon, source}` or null) and `tz_override`.
- `PATCH /v1/entries/{id}`: `location` (`{name, lat, lon}`, or `null` for "use automatic location") and `tz_override` (IANA name or `null`). Editors only, as for other edits. An unknown time zone is a 422.
- `PATCH /v1/diaries/{id}`: `location` the same way, owner only, as today for diary settings.
- `GET /v1/geocode?q=...`: city search for the boxes; signed-in users only; rate-limited; returns up to 5 results `{name, admin1, country, lat, lon, timezone}`. It proxies Open-Meteo, so the browser never calls Open-Meteo directly.
- `_resolve_lat_lon` in `workers/enrichments.py` reads the entry's place first.

## Privacy

- Calendar location text goes to Open-Meteo (decision 1). Photo GPS never leaves the server.
- The cache is keyed by a SHA-256 hash of the location text, not the text itself, and holds only the resulting town and coordinates. It lives in Redis with a 90-day expiry and needs no table. It is shared, not tied to a diary, so hard delete does not remove entries from it; they expire.
- Synced events stay owner-only (S-11). The entry shows only the derived town, which is part of the entry.

## Calls I made (please check)

1. **Home location in diary settings** is added. It is beyond what we discussed, but without it the last fallback never applies (no diary has one).
2. **Photo GPS beats calendar text.** A photo shows where the child actually was; an event says where it was planned.
3. **The manual entry form is unchanged.** The place is set on the entry page, keeping the quick form quick.
4. **The Open-Meteo credit** is added, including to the existing weather badge, because the licence requires it.
5. **Photo places have no name** ("From a photo"), because there is no reverse geocoding.
6. **The cache lives in Redis** with a 90-day expiry, keyed by a hash of the text, not in a new table.
7. **The browser's time zone list** is used for the picker. The diary settings time zone stays a free text field.
8. **Without an override, event times keep showing in the viewer's own time zone**, as today.

## Left to the builder

- Exact layout and wording of the controls, within the behaviour above.
- How the city search debounces and shows "no matches".
- Rate-limit numbers for `/v1/geocode`.

## Verification

- **Unit tests** for the strict rules, using the shapes from the test above: ZIP after a state; a house number that looks like a ZIP; venue first part; full state name; no state; lone phrase; theme park; non-US text. Each with the expected place or no match.
- **Integration tests:**
  - The order parent > photo > event > home > none.
  - A parent place survives regeneration.
  - "Use automatic location" restores the automatic place.
  - A place change refetches weather.
  - A failed lookup is not cached and leaves the entry on the next source.
  - The time zone moves the date for an entry with events and leaves an entry without events alone.
  - An unknown time zone gives 422; viewers can't change the place.
  - `/v1/geocode` needs sign-in.
- **Browser tests:**
  - The place and the credit show on the entry.
  - The city search sets the place.
  - "Use automatic location".
  - The advanced time zone is hidden by default, and changes the shown times when set.
  - Home location in diary settings.
- **`make test-all`.**
- **A manual check on the local stack** with Andrew's real calendar: count how many entries get a place automatically, and look at a sample to confirm none is wrong.
