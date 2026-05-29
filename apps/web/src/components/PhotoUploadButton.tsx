"use client";
import { useState } from "react";
import { api, Photo } from "../lib/api";

interface PhotoUploadButtonProps {
  onUploaded: (photo: Photo) => void;
  className?: string;
}

export function PhotoUploadButton({ onUploaded, className }: PhotoUploadButtonProps) {
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      setProgress(null);
    }
  }

  return (
    <label className={className}>
      Upload photo
      <input type="file" accept="image/*" onChange={handleChange} style={{ display: "none" }} />
      {progress !== null && <span> {Math.round(progress * 100)}%</span>}
      {error && <span role="alert">{error}</span>}
    </label>
  );
}
