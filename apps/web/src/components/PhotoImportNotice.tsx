"use client";
import { useEffect, useState } from "react";
import { api, type PhotoImport } from "../lib/api";
import { navigateTo } from "../lib/navigate";

interface Props {
  entryId: string;
  /** Change this to reload the list (e.g. after an import finishes). */
  refreshKey?: number;
  onDismiss?: () => void;
}

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

export function PhotoImportNotice({ entryId, refreshKey = 0, onDismiss }: Props) {
  const [imports, setImports] = useState<PhotoImport[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.photoImports
      .listForEntry(entryId)
      .then((l) => {
        if (!cancelled) setImports(l);
      })
      .catch(() => {
        /* a notice that can't load is not worth an error on the entry */
      });
    return () => {
      cancelled = true;
    };
  }, [entryId, refreshKey]);

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

  const shown = imports.filter((i) => importMessage(i) !== null);
  if (shown.length === 0) return null;

  return (
    <div style={{ display: "grid", gap: "0.5rem", margin: "0.75rem 0" }}>
      {shown.map((imp) => (
        <div key={imp.id} className="card" role="status" style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>{importMessage(imp)}</span>
          {imp.status === "needs_reconnect" && (
            <button type="button" className="btn btn-secondary" onClick={() => void reconnect()}>
              Reconnect Google Photos
            </button>
          )}
          <button type="button" className="btn btn-secondary" onClick={() => void dismiss(imp.id)}>
            Dismiss
          </button>
        </div>
      ))}
      {error && <p role="alert" className="error-message">{error}</p>}
    </div>
  );
}
