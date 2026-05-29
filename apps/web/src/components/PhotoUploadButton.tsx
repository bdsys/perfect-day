"use client";
import { useRef, useState } from "react";
import { api, Photo } from "../lib/api";

interface Props {
  onUploaded: (photo: Photo) => void;
  className?: string;
  label?: string;
}

export function PhotoUploadButton({ onUploaded, className, label = "Upload photo" }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = progress !== null;

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const meta = await api.photos.requestUploadUrl({
        declared_mime: file.type,
        declared_size: file.size,
      });
      await api.photos.uploadFile(meta.upload_url, file, setProgress);
      const photo = await api.photos.finalize(meta.photo_id);
      onUploaded(photo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      e.target.value = "";
      setProgress(null);
    }
  }

  return (
    <>
      <button
        type="button"
        className={className ?? "btn btn-primary"}
        onClick={() => inputRef.current?.click()}
        disabled={busy}
      >
        {busy ? `Uploading… ${Math.round((progress ?? 0) * 100)}%` : label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        onChange={handleChange}
        style={{ display: "none" }}
      />
      {error && (
        <span role="alert" style={{ marginLeft: "0.5rem", color: "var(--error)" }}>
          {error}
        </span>
      )}
    </>
  );
}
