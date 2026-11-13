"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Entry } from "../lib/api";
import { navigateTo } from "../lib/navigate";

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
  /** An import was created; the page now tracks it. */
  onStarted: () => void;
}

type Phase = "loading" | "not_connected" | "ready";

export function GooglePhotosPick({ entry, inFlight, onStarted }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const [connecting, setConnecting] = useState(false);
  const connectingRef = useRef(false);
  const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offerConnect, setOfferConnect] = useState(false);

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
        if (!cancelled) setPhase("not_connected");
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  function start() {
    if (startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    setError(null);
    setOfferConnect(false);
    setFallbackUrl(null);
    // Must be synchronous inside the click, or the browser blocks the new tab.
    const tab = window.open("", "_blank");
    void (async () => {
      try {
        const imp = await api.photoImports.create(entry.id);
        const url = `${imp.picker_uri}/autoclose`;
        if (tab) tab.location.href = url;
        else setFallbackUrl(url);
        onStarted();
      } catch (e) {
        tab?.close();
        if (e instanceof ApiError && e.status === 409) {
          setOfferConnect(true);
          setError("Google Photos isn't connected yet.");
        } else if (e instanceof ApiError && e.status === 503) {
          setError("Google Photos isn't responding. Try again in a minute.");
        } else {
          setError("Couldn't start Google Photos.");
        }
      } finally {
        startingRef.current = false;
        setStarting(false);
      }
    })();
  }

  if (phase === "loading") return null;

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
        <p>Google Photos is open. Finish your pick there.</p>
        {fallbackUrl && (
          <p>
            <a href={fallbackUrl} target="_blank" rel="noopener noreferrer">Open Google Photos</a>
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <button type="button" className="btn btn-secondary" onClick={start} disabled={starting}>
        Search Google Photos for {formatEntryDate(entry.entry_date)}
      </button>
      {error && <p role="alert" className="error-message">{error}</p>}
      {offerConnect && (
        <button type="button" className="btn btn-secondary" onClick={() => void connect()} disabled={connecting}>
          Connect Google Photos
        </button>
      )}
    </div>
  );
}
