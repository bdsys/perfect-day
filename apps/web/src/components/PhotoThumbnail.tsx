"use client";
import { usePhotoBlob } from "../lib/usePhotoBlob";

interface PhotoThumbnailProps {
  photoId: string;
  alt: string;
  onClick?: () => void;
  className?: string;
}

export function PhotoThumbnail({ photoId, alt, onClick, className }: PhotoThumbnailProps) {
  const { src, error } = usePhotoBlob(photoId, "thumb");

  if (error) return <div className={className} aria-label="failed to load" />;
  if (!src) return <div className={className} aria-busy="true" />;
  // eslint-disable-next-line @next/next/no-img-element -- blob: URL from encrypted storage, next/image cannot optimize
  return <img src={src} alt={alt} onClick={onClick} className={className} />;
}
