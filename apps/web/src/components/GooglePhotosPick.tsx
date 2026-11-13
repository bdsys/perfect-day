"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Entry } from "../lib/api";
import { navigateTo } from "../lib/navigate";
import { usePolling } from "../lib/usePolling";

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
  /** Called when the import has finished (any final state). */
  onDone: () => void;
}

type Phase = "loading" | "not_connected" | "ready" | "waiting";

export function GooglePhotosPick({ entry, onDone }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [importId, setImportId] = useState<string | null>(null);
  const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offerConnect, setOfferConnect] = useState(false);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

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
    setError(null);
    try {
      const { url } = await api.integrations.getGoogleAuthUrl("photos", `/entries/${entry.id}`);
      navigateTo(url);
    } catch {
      setError("Couldn't start Google. Try again.");
    }
  }

  function start() {
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
        setImportId(imp.id);
        setPhase("waiting");
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
      }
    })();
  }

  const poll = useCallback(async () => {
    if (!importId) return;
    try {
      const imp = await api.photoImports.get(importId);
      if (imp.status !== "waiting" && imp.status !== "copying") {
        setPhase("ready");
        setImportId(null);
        setFallbackUrl(null);
        onDoneRef.current();
      }
    } catch {
      /* keep waiting; the next poll tries again */
    }
  }, [importId]);

  usePolling(poll, 3000, phase === "waiting" && importId !== null);

  async function cancel() {
    if (!importId) return;
    const id = importId;
    setImportId(null);
    setPhase("ready");
    setFallbackUrl(null);
    try {
      await api.photoImports.cancel(id);
    } catch {
      /* it will expire on its own */
    }
  }

  if (phase === "loading") return null;

  if (phase === "not_connected") {
    return (
      <div>
        <button type="button" className="btn btn-secondary" onClick={() => void connect()}>
          Connect Google Photos
        </button>
        {error && <p role="alert" className="error-message">{error}</p>}
      </div>
    );
  }

  if (phase === "waiting") {
    return (
      <div>
        <p role="status">Waiting for you to finish in Google Photos…</p>
        {fallbackUrl && (
          <p>
            <a href={fallbackUrl} target="_blank" rel="noopener noreferrer">Open Google Photos</a>
          </p>
        )}
        <button type="button" className="btn btn-secondary" onClick={() => void cancel()}>
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div>
      <button type="button" className="btn btn-secondary" onClick={start}>
        Search Google Photos for {formatEntryDate(entry.entry_date)}
      </button>
      {error && <p role="alert" className="error-message">{error}</p>}
      {offerConnect && (
        <button type="button" className="btn btn-secondary" onClick={() => void connect()}>
          Connect Google Photos
        </button>
      )}
    </div>
  );
}
