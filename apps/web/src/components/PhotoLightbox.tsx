"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface PhotoLightboxProps {
  photoIds: string[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}

export function PhotoLightbox({ photoIds, index, onIndexChange, onClose }: PhotoLightboxProps) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setSrc(null);
    api.photos.get(photoIds[index], "full").then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    }).catch(() => setSrc(null));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoIds[index]]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" && index < photoIds.length - 1) onIndexChange(index + 1);
      else if (e.key === "ArrowLeft" && index > 0) onIndexChange(index - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, photoIds.length, onClose, onIndexChange]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.85)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
    >
      {src && (
        <img
          src={src}
          alt=""
          onClick={(e) => e.stopPropagation()}
          style={{ maxHeight: "90vh", maxWidth: "90vw", objectFit: "contain" }}
        />
      )}
    </div>
  );
}
