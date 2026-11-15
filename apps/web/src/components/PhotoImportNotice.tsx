"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type PhotoImport } from "../lib/api";
import { navigateTo } from "../lib/navigate";

interface Props {
  entryId: string;
  /** Change this to reload the list (e.g. after an import finishes). */
  refreshKey?: number;
  onDismiss?: () => void;
  /** An import that was in flight has reached a final state: refresh the entry. */
  onFinished?: () => void;
  /** inFlight: any import is waiting or copying. copying: one of them has moved on to copying. */
  onInFlightChange?: (inFlight: boolean, copying: boolean) => void;
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

export function PhotoImportNotice({ entryId, refreshKey = 0, onDismiss, onFinished, onInFlightChange }: Props) {
  const [imports, setImports] = useState<PhotoImport[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const reconnectingRef = useRef(false);
  const failuresRef = useRef(0);
  const [gaveUp, setGaveUp] = useState(false);

  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const onInFlightRef = useRef(onInFlightChange);
  onInFlightRef.current = onInFlightChange;
  const inFlightIds = useRef<Set<string>>(new Set());
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
      const finished = [...inFlightIds.current].some((id) => !now.has(id));
      inFlightIds.current = now;
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
  }, [entryId]);

  useEffect(() => {
    failuresRef.current = 0;
    setGaveUp(false);
    void load();
  }, [load, refreshKey]);

  const anyInFlight = imports.some(isInFlight);
  useEffect(() => {
    if (!anyInFlight || gaveUp) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [anyInFlight, gaveUp, load]);

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

  const shown = imports.filter((i) => isInFlight(i) || importMessage(i) !== null);
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
