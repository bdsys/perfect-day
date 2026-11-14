# Turning on Google Photos for Perfect Day (Photos Picker API)

Written 2026-11-14 from Google's docs. The sources are linked under each step.

**Do this once in each Google Cloud project that runs picks.** That means `perfect-day-dev` (localhost) now, and `perfect-day-prod` (the NUC) before the phone checks. CI never calls Google, so `perfect-day-test` doesn't need it. Google turns APIs on per project. `docs/local-dev.md` plans three projects, but whether all three exist hasn't been checked.

**Why:** if the API isn't enabled, connecting Google Photos still "works", but starting a pick fails. Google refuses, and the app currently shows "Google Photos isn't connected yet." Plan B Task 0 changes that message so it says the API is off.

## 1. Enable the Photos Picker API

1. Open <https://console.developers.google.com/apis/library>.
2. Pick the project from the project menu at the top.
3. Go to **APIs & Services → Library**, search for **"Photos"**, open the **Google Photos Picker API**, and click **Enable**.
   - **Careful:** don't pick the similarly named **"Google Picker API"**. That one is for Google Drive. Google warns: *"you may also see the "Google Picker API", which is different from the Google Photos Picker API."*

Source: <https://developers.google.com/photos/overview/configure-your-app> ("Enable the API").

## 2. Add the picker scope to the consent screen

1. Go to **Menu → Google Auth platform → Data Access**.
2. Click **Add or Remove Scopes**.
3. Find `https://www.googleapis.com/auth/photospicker.mediaitems.readonly` and tick it.
   - If it isn't in the list, type it into **Manually add scopes**.
   - Google: *"Only scopes for enabled APIs are listed in the scopes table"*, so do step 1 first.
4. Click **Update**, then **Save**.
5. Check that `https://www.googleapis.com/auth/calendar.readonly` is still listed. You are adding a scope, not replacing one.

Sources: <https://developers.google.com/workspace/guides/configure-oauth-consent>, <https://support.google.com/cloud/answer/15549135>.

## 3. Check you're allowed in as a test user

The app is unverified, so its project is most likely in **Testing** status.

1. Go to **Google Auth platform → Audience**.
2. Under **Test users**, make sure your Google account is listed. Use the same account you use for Calendar. If it isn't, click **Add users**, add it, and save.

Things to know while it's in Testing:
- Google shows an "unverified app" warning before consent. That's expected.
- **Your consent expires after 7 days, refresh token included.** Google: *"Authorizations by a test user will expire seven days from the time of consent."* The exception covers only the email, profile and openid scopes, so it doesn't apply to Calendar or Photos. Expect "Reconnect Google" about weekly until the app is verified. This also explains Calendar connections "dying" after a week.
- At most 100 test users.

Source: <https://support.google.com/cloud/answer/15549945> ("Publishing status").

## 4. Nothing to change on the OAuth client

The existing Web client already has the redirect URI the app uses, for example `http://localhost:8000/v1/integrations/google/callback` for dev (`docs/local-dev.md`). The Picker uses the same client and the same redirect.

One rule to keep in mind: *"Resources created through any of the Google Photos APIs can only be accessed or modified using the original client ID used to create them."* For the Picker those resources are sessions, so don't swap client IDs mid-pick. Source: <https://developers.google.com/photos/overview/configure-your-app>.

## 5. Try it

1. In the app, open an entry → **Add photos → From Google Photos**.
2. If it asks you to, click **Connect Google Photos** and approve both permissions.
3. Click **Search Google Photos**. A Google Photos tab should open.
4. Pick a photo and click Done. Back in the app, the photo should appear on the entry within a few seconds.

If it still says "not connected", tell Claude. The next step is to read Google's actual refusal reason, which Plan B Task 0 logs.

## Before going public (not needed for the PoC)

The Photos APIs require Google's app review: *"Before you can launch a public application that accesses the Photos APIs, your app must be reviewed by Google."* Source: <https://developers.google.com/photos/overview/configure-your-app>.

## Not stated by Google's docs

- Whether the picker scope counts as "sensitive". That affects verification and the 100-user cap.
- The API's exact name in the Library. The docs only say to search for "Photos". "Google Photos Picker API" is the name Google uses on its own pages.
