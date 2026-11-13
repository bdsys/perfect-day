"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PhotoImport } from "../lib/api";
import { navigateTo } from "../lib/navigate";

interface Props {
  entryId: string;
  /** Change this to reload the list (e.g. after an import finishes). */
  refreshKey?: number;
  onDismiss?: () => void;
  /** An import that was in flight has reached a final state: refresh the entry. */
  onFinished?: () => void;
  /** True while any import is waiting or copying. */
  onInFlightChange?: (inFlight: boolean) => void;
}

const POLL_MS = 3000;
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
      const now = new Set(list.filter(isInFlight).map((i) => i.id));
      const finished = [...inFlightIds.current].some((id) => !now.has(id));
      inFlightIds.current = now;
      setImports(list);
      onInFlightRef.current?.(now.size > 0);
      if (finished) onFinishedRef.current?.();
    } catch {
      /* a notice that can't load is not worth an error on the entry */
    }
  }, [entryId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const anyInFlight = imports.some(isInFlight);
  useEffect(() => {
    if (!anyInFlight) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [anyInFlight, load]);

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
    setError(null);
    try {
      const { url } = await api.integrations.getGoogleAuthUrl("photos", `/entries/${entryId}`);
      navigateTo(url);
    } catch {
      setError("Couldn't start Google. Try again.");
    }
  }

  const shown = imports.filter((i) => isInFlight(i) || importMessage(i) !== null);
  if (shown.length === 0) return null;

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
          {imp.status === "waiting" && (
            <button type="button" className="btn btn-secondary" onClick={() => void cancel(imp.id)}>
              Cancel
            </button>
          )}
          {imp.status === "needs_reconnect" && (
            <button type="button" className="btn btn-secondary" onClick={() => void reconnect()}>
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
      {error && <p role="alert" className="error-message">{error}</p>}
    </div>
  );
}
