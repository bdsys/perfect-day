"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface PhotoThumbnailProps {
  photoId: string;
  alt: string;
  onClick?: () => void;
  className?: string;
}

export function PhotoThumbnail({ photoId, alt, onClick, className }: PhotoThumbnailProps) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setError(null);
    api.photos.get(photoId, "thumb").then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    }).catch(() => {
      if (!cancelled) setError("failed");
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoId]);

  if (error) return <div className={className} aria-label="failed to load" />;
  if (!src) return <div className={className} aria-busy="true" />;
  // eslint-disable-next-line @next/next/no-img-element -- blob: URL from encrypted storage, next/image cannot optimize
  return <img src={src} alt={alt} onClick={onClick} className={className} />;
}
