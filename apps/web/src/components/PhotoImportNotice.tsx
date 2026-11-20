"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type PhotoImport } from "../lib/api";
import { navigateTo } from "../lib/navigate";
import { useResetOnBfcacheRestore } from "../lib/useResetOnBfcacheRestore";
import { useLatest } from "../lib/useLatest";

interface Props {
  entryId: string;
  /** Change this to reload the list (e.g. after an import finishes). */
  refreshKey?: number;
  onDismiss?: () => void;
  /** An import that was in flight has reached a final state: refresh the entry. */
  onFinished?: () => void;
  /** inFlight: any import is waiting or copying. copying: one of them has moved on to copying. */
  onInFlightChange?: (inFlight: boolean, copying: boolean) => void;
  /** A pick was just started in another tab: poll even with nothing in flight yet. */
  watching?: boolean;
}

const POLL_MS = 3000;
const MAX_POLL_FAILURES = 3;
const CANT_CHECK = "We can't check on your Google Photos pick right now.";
const isInFlight = (i: PhotoImport) => i.status === "waiting" || i.status === "copying";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function importMessage(imp: PhotoImport): string | null {
  switch (imp.status) {
    case "done": {
      const parts: string[] = [];
      if (imp.added_count > 0) parts.push(`${plural(imp.added_count, "photo", "photos")} added from Google Photos`);
      if (imp.skipped_videos > 0) parts.push(`${plural(imp.skipped_videos, "video", "videos")} skipped`);
      if (imp.failed_names.length > 0) {
        const n = imp.failed_names.length;
        parts.push(
          `${n} couldn't be copied (${imp.failed_names.join(", ")}): pick ${n === 1 ? "it" : "them"} again from Google Photos`,
        );
      }
      return parts.length > 0 ? parts.join(" · ") : "No photos were picked in Google Photos.";
    }
    case "failed":
      return "We couldn't copy your photos from Google Photos. Try again.";
    case "expired":
      return "That Google Photos pick timed out before it finished. Try again.";
    case "needs_reconnect":
      return "Google Photos needs to be connected again before we can copy your photos.";
    default:
      return null; // waiting, copying and cancelled are not results
  }
}

export function PhotoImportNotice({ entryId, refreshKey = 0, onDismiss, onFinished, onInFlightChange, watching = false }: Props) {
  const [imports, setImports] = useState<PhotoImport[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const reconnectingRef = useRef(false);
  const failuresRef = useRef(0);
  const [gaveUp, setGaveUp] = useState(false);

  // A new pick (refreshKey) or another entry starts checking afresh: drop "can't check"
  // in the same render, the way React adjusts state when a prop changes.
  const [checkedFor, setCheckedFor] = useState({ entryId, refreshKey });
  if (checkedFor.entryId !== entryId || checkedFor.refreshKey !== refreshKey) {
    setCheckedFor({ entryId, refreshKey });
    setGaveUp(false);
  }

  const onFinishedRef = useLatest(onFinished);
  const onInFlightRef = useLatest(onInFlightChange);
  const inFlightIds = useRef<Set<string>>(new Set());
  // Every import id seen so far (null until the first load). An id that first appears
  // already finished was picked in another tab while this one wasn't looking.
  const seenIds = useRef<Set<string> | null>(null);
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const list = await api.photoImports.listForEntry(entryId);
      if (!aliveRef.current) return;
      failuresRef.current = 0;
      const now = new Set(list.filter(isInFlight).map((i) => i.id));
      const appearedDone =
        seenIds.current !== null && list.some((i) => !seenIds.current!.has(i.id) && !isInFlight(i));
      const finished = appearedDone || [...inFlightIds.current].some((id) => !now.has(id));
      inFlightIds.current = now;
      seenIds.current = new Set([...(seenIds.current ?? []), ...list.map((i) => i.id)]);
      setImports(list);
      onInFlightRef.current?.(now.size > 0, list.some((i) => i.status === "copying"));
      if (finished) onFinishedRef.current?.();
    } catch (e) {
      if (!aliveRef.current) return;
      if (e instanceof ApiError && e.status === 404) {
        // The import is gone: nothing is in flight any more, and there is nothing to report.
        failuresRef.current = 0;
        inFlightIds.current = new Set();
        setImports((prev) => prev.filter((i) => !isInFlight(i)));
        onInFlightRef.current?.(false, false);
        return;
      }
      failuresRef.current += 1;
      if (failuresRef.current >= MAX_POLL_FAILURES) {
        // We no longer know the state: don't leave "Copying…" showing next to "can't check".
        setGaveUp(true);
        onInFlightRef.current?.(false, false);
      }
    }
  }, [entryId, onFinishedRef, onInFlightRef]);

  useEffect(() => {
    failuresRef.current = 0;
    // load() sets state only after its first await, so this cannot cascade renders.
    // react-hooks 7.x flags any async call that sets state (row 61, probed 2026-11-19).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, refreshKey]);

  // Coming back to this tab (iOS pauses background tabs while Google Photos is in front).
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  const anyInFlight = imports.some(isInFlight);
  useEffect(() => {
    if ((!anyInFlight && !watching) || gaveUp) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [anyInFlight, watching, gaveUp, load]);

  async function cancel(id: string) {
    setError(null);
    try {
      await api.photoImports.cancel(id);
    } catch {
      setError("Couldn't cancel. Try again.");
    }
    void load();
  }

  async function dismiss(id: string) {
    setError(null);
    try {
      await api.photoImports.dismiss(id);
      setImports((prev) => prev.filter((i) => i.id !== id));
      onDismiss?.();
    } catch {
      setError("Couldn't dismiss this. Try again.");
    }
  }

  useResetOnBfcacheRestore(() => {
    reconnectingRef.current = false;
    setReconnecting(false);
  });

  async function reconnect() {
    if (reconnectingRef.current) return; // two authorize calls would leave the wrong connect cookie
    reconnectingRef.current = true;
    setReconnecting(true);
    setError(null);
    try {
      const { url } = await api.integrations.getGoogleAuthUrl("photos", `/entries/${entryId}`);
      navigateTo(url);
    } catch {
      reconnectingRef.current = false;
      setReconnecting(false);
      setError("Couldn't start Google. Try again.");
    }
  }

  const shown = imports.filter((i) => (!gaveUp && isInFlight(i)) || importMessage(i) !== null);
  if (shown.length === 0 && !gaveUp) return null;

  return (
    <div style={{ display: "grid", gap: "0.5rem", margin: "0.75rem 0" }}>
      {shown.map((imp) => (
        <div key={imp.id} className="card" role="status" style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>
            {imp.status === "waiting"
              ? "Waiting for you to finish in Google Photos…"
              : imp.status === "copying"
                ? "Copying photos from Google Photos…"
                : importMessage(imp)}
          </span>
          {isInFlight(imp) && (
            <button type="button" className="btn btn-secondary" onClick={() => void cancel(imp.id)}>
              Cancel
            </button>
          )}
          {imp.status === "needs_reconnect" && (
            <button type="button" className="btn btn-secondary" onClick={() => void reconnect()} disabled={reconnecting}>
              Reconnect Google Photos
            </button>
          )}
          {!isInFlight(imp) && (
            <button type="button" className="btn btn-secondary" onClick={() => void dismiss(imp.id)}>
              Dismiss
            </button>
          )}
        </div>
      ))}
      {gaveUp && <p role="alert" className="error-message">{CANT_CHECK}</p>}
      {error && <p role="alert" className="error-message">{error}</p>}
    </div>
  );
}
