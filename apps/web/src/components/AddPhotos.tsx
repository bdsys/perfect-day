"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Entry, type Photo } from "../lib/api";
import { PhotoThumbnail } from "./PhotoThumbnail";

const MAX_CONCURRENT = 3;
const MAX_BYTES = 52428800; // 50 MB, the API's limit
const TOO_BIG = "This photo is over 50 MB.";

function friendlyUploadError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 413 || e.code === "size_exceeds_limit") return TOO_BIG;
    if (e.status === 415 || e.code === "unsupported_mime") return "This file isn't a photo we can use.";
    if (e.status === 401 || e.code === "unauthorized") return "You've been signed out. Sign in again and try.";
  } else if (e instanceof Error && e.message === "upload network error") {
    return "The upload didn't finish. Check your connection and try again.";
  }
  return "Couldn't add this photo.";
}

interface Props {
  entry: Entry;
  onChanged: (entry: Entry) => void;
  /** Slot for the "From Google Photos" option (filled by the Google Photos task). */
  googlePhotos?: React.ReactNode;
}

type Item = {
  id: number;
  file: File;
  status: "queued" | "uploading" | "done" | "error";
  progress: number;
  error?: string;
};

export function AddPhotos({ entry, onChanged, googlePhotos }: Props) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [library, setLibrary] = useState<Photo[] | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [attachedNow, setAttachedNow] = useState<Set<string>>(new Set());
  const [attachError, setAttachError] = useState<string | null>(null);
  const nextId = useRef(1);
  const entryRef = useRef(entry);
  entryRef.current = entry;
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const filesRef = useRef(new Map<number, File>());

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLibraryError(null);
    api.photos
      .listForUser(entry.entry_date)
      .then((p) => {
        if (!cancelled) setLibrary(p);
      })
      .catch((e) => {
        if (!cancelled) setLibraryError(e instanceof Error ? e.message : "Failed to load your library");
      });
    return () => {
      cancelled = true;
    };
    // Reload only when the panel opens (or the date changes), not on every attach.
  }, [open, entry.entry_date]);

  const patch = useCallback((id: number, change: Partial<Item>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...change } : it)));
  }, []);

  // One queue and one pool for the whole component, so at most MAX_CONCURRENT
  // uploads run at once no matter how many picks or retries happen.
  const queueRef = useRef<number[]>([]);
  const activeRef = useRef(0);
  const anyDoneRef = useRef(false);

  const pump = useCallback(() => {
    while (activeRef.current < MAX_CONCURRENT && queueRef.current.length > 0) {
      const id = queueRef.current.shift()!;
      const file = filesRef.current.get(id)!;
      activeRef.current++;
      patch(id, { status: "uploading", progress: 0, error: undefined });
      void (async () => {
        try {
          const photo = await api.photos.upload(file, (p) => patch(id, { progress: p }));
          await api.photos.attachToEntry(entryRef.current.id, photo.id);
          anyDoneRef.current = true;
          filesRef.current.delete(id);
          patch(id, { status: "done", progress: 1 });
        } catch (e) {
          patch(id, { status: "error", error: friendlyUploadError(e) });
        } finally {
          activeRef.current--;
          pump();
          if (activeRef.current === 0 && queueRef.current.length === 0 && anyDoneRef.current) {
            anyDoneRef.current = false;
            try {
              onChangedRef.current(await api.entries.get(entryRef.current.id));
            } catch {
              /* the photos are attached; the strip refreshes on next load */
            }
          }
        }
      })();
    }
  }, [patch]);

  const enqueue = useCallback(
    (ids: number[]) => {
      queueRef.current.push(...ids);
      pump();
    },
    [pump],
  );

  function handleFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (files.length === 0) return;
    const toUpload: number[] = [];
    const added: Item[] = files.map((file) => {
      const id = nextId.current++;
      if (file.size > MAX_BYTES) return { id, file, status: "error", progress: 0, error: TOO_BIG };
      filesRef.current.set(id, file);
      toUpload.push(id);
      return { id, file, status: "queued", progress: 0 };
    });
    setItems((prev) => [...prev, ...added]);
    enqueue(toUpload);
  }

  async function attachFromLibrary(photoId: string) {
    setAttachError(null);
    try {
      await api.photos.attachToEntry(entry.id, photoId);
      setAttachedNow((prev) => new Set(prev).add(photoId));
      onChanged(await api.entries.get(entry.id));
    } catch (e) {
      setAttachError(e instanceof Error ? e.message : "Failed to attach photo");
    }
  }

  const attachedIds = new Set([...entry.photos.map((p) => p.id), ...attachedNow]);
  const available = (library ?? []).filter((p) => !attachedIds.has(p.id));

  return (
    <div className="add-photos" style={{ margin: "0.75rem 0" }}>
      <button type="button" className="btn btn-secondary" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        Add photos
      </button>
      {open && (
        <div style={{ marginTop: "0.75rem", display: "grid", gap: "1rem" }}>
          <div role="group" aria-label="From this phone">
            <h3>From this phone</h3>
            <input type="file" accept="image/*" multiple onChange={handleFiles} aria-label="Choose photos" />
            {items.length > 0 && (
              <ul style={{ listStyle: "none", padding: 0, margin: "0.5rem 0 0" }}>
                {items.map((it) => (
                  <li key={it.id} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <span>{it.file.name}</span>
                    {it.status === "queued" && <span>Waiting…</span>}
                    {it.status === "uploading" && (
                      <progress value={it.progress} max={1} aria-label={`Uploading ${it.file.name}`} />
                    )}
                    {it.status === "done" && <span>Added</span>}
                    {it.status === "error" && (
                      <>
                        <span role="alert" style={{ color: "var(--error)" }}>{it.error}</span>
                        {it.file.size <= MAX_BYTES && (
                        <button type="button" onClick={() => enqueue([it.id])}>
                          Try again
                        </button>
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div role="group" aria-label="From your library">
            <h3>From your library</h3>
            {libraryError && <p role="alert">{libraryError}</p>}
            {attachError && <p role="alert">{attachError}</p>}
            {library !== null && available.length === 0 && <p>No other photos in your library.</p>}
            <ul className="photo-grid">
              {available.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    aria-label="Add this photo to the entry"
                    onClick={() => void attachFromLibrary(p.id)}
                    style={{ padding: 0, border: 0, background: "none", cursor: "pointer" }}
                  >
                    <PhotoThumbnail photoId={p.id} alt="" className="thumbnail" />
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Slot for the Google Photos option (Task 9 passes the `googlePhotos` prop). */}
          {googlePhotos && (
            <div role="group" aria-label="From Google Photos">
              <h3>From Google Photos</h3>
              {googlePhotos}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
