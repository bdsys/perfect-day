"use client";
import { useEffect, useRef, useState } from "react";
import { api, type Entry } from "../lib/api";
import { navigateTo } from "../lib/navigate";
import { useResetOnBfcacheRestore } from "../lib/useResetOnBfcacheRestore";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-05-16" -> "16 May 2026", without going through Date (no timezone shift). */
export function formatEntryDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d || m < 1 || m > 12) return iso;
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

interface Props {
  entry: Entry;
  /** True while the page knows of an import that is waiting or copying. */
  inFlight: boolean;
  /** True once the import has moved from waiting for the user to copying. */
  copying?: boolean;
  /** An import was created; the page now tracks it. */
  onStarted: () => void;
  /** True when the entry can take no more photos. */
  disabled?: boolean;
  /** True from the tap until the page sees the import, or a minute passes. The page watches meanwhile. */
  onOpeningChange?: (opening: boolean) => void;
}

/** How long to wait for the new tab to create its import before offering the button again. */
const OPENING_TIMEOUT_MS = 60_000;

/** Our own page that starts the picker session in the new tab, then replaces itself with Google's picker. */
export const pickerOpenPath = (entryId: string) => `/google-photos/open?entry=${encodeURIComponent(entryId)}`;

type Phase = "loading" | "error" | "not_connected" | "ready";

export function GooglePhotosPick({ entry, inFlight, copying = false, onStarted, disabled = false, onOpeningChange }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [opening, setOpening] = useState(false);
  const openingRef = useRef(false);
  const onOpeningChangeRef = useRef(onOpeningChange);
  onOpeningChangeRef.current = onOpeningChange;
  const [connecting, setConnecting] = useState(false);
  const connectingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const [checkKey, setCheckKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.integrations
      .list()
      .then((list) => {
        if (cancelled) return;
        const g = list.find((i) => i.provider === "google" && !i.revoked);
        setPhase(g?.photos ? "ready" : "not_connected");
      })
      .catch(() => {
        if (!cancelled) setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [checkKey]);

  function recheck() {
    setPhase("loading");
    setCheckKey((k) => k + 1);
  }

  useResetOnBfcacheRestore(() => {
    connectingRef.current = false;
    setConnecting(false);
  });

  async function connect() {
    if (connectingRef.current) return; // two authorize calls would leave the wrong connect cookie
    connectingRef.current = true;
    setConnecting(true);
    setError(null);
    try {
      const { url } = await api.integrations.getGoogleAuthUrl("photos", `/entries/${entry.id}`);
      navigateTo(url);
    } catch {
      connectingRef.current = false;
      setConnecting(false);
      setError("Couldn't start Google. Try again.");
    }
  }

  function setOpeningState(value: boolean) {
    openingRef.current = value;
    setOpening(value);
    onOpeningChangeRef.current?.(value);
  }

  // The page found the import the new tab created: stop "opening" and show its progress.
  useEffect(() => {
    if (inFlight && openingRef.current) setOpeningState(false);
  }, [inFlight]);

  // No import after a minute: the tab may have been blocked, closed, or shown an error.
  useEffect(() => {
    if (!opening) return;
    const id = setTimeout(() => {
      setOpeningState(false);
      setError("Didn't see Google Photos open? Try again.");
    }, OPENING_TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [opening]);

  function start() {
    if (openingRef.current) return;
    setError(null);
    // Inside the tap, a real address: every browser opens it. The new tab starts the session
    // and moves itself to Google (iOS Chrome ignored a blank tab sent there afterwards).
    // noopener: Google's page can't reach back into ours.
    window.open(pickerOpenPath(entry.id), "_blank", "noopener");
    setOpeningState(true);
    onStarted();
  }

  if (phase === "loading") return null;

  if (phase === "error") {
    return (
      <div>
        <p role="alert" className="error-message">{"Couldn't check your Google connection. Try again."}</p>
        <button type="button" className="btn btn-secondary" onClick={recheck}>
          Try again
        </button>
      </div>
    );
  }

  if (phase === "not_connected") {
    return (
      <div>
        <button type="button" className="btn btn-secondary" onClick={() => void connect()} disabled={connecting}>
          Connect Google Photos
        </button>
        {error && <p role="alert" className="error-message">{error}</p>}
      </div>
    );
  }

  if (inFlight) {
    return (
      <div>
        <p>{copying ? "Copying your photos from Google Photos…" : "Google Photos is open. Finish your pick there."}</p>
      </div>
    );
  }

  if (opening) {
    return (
      <div>
        <p>Opening Google Photos in a new tab…</p>
        <p>
          <a href={pickerOpenPath(entry.id)} target="_blank" rel="noopener noreferrer">Open Google Photos</a>
        </p>
      </div>
    );
  }

  return (
    <div>
      <button type="button" className="btn btn-secondary" onClick={start} disabled={disabled}>
        Search Google Photos for {formatEntryDate(entry.entry_date)}
      </button>
      {error && <p role="alert" className="error-message">{error}</p>}
    </div>
  );
}
