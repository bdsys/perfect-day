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

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    api.photos.get(photoId, "thumb").then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoId]);

  if (!src) return <div className={className} aria-busy="true" />;
  return <img src={src} alt={alt} onClick={onClick} className={className} />;
}
