"use client";
import { useRef, useState } from "react";
import { api, Photo } from "../lib/api";

interface Props {
  onUploaded: (photo: Photo) => void;
  className?: string;
  label?: string;
  multiple?: boolean;
}

export function PhotoUploadButton({ onUploaded, className, label = "Upload photo", multiple = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = progress !== null;

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const files = Array.from(input.files ?? []);
    if (files.length === 0) return;
    setError(null);
    setProgress(0);
    try {
      for (const file of files) {
        setProgress(0);
        const photo = await api.photos.upload(file, setProgress);
        onUploaded(photo);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      input.value = "";
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
        multiple={multiple}
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
