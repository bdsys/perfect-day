"use client";
import { useEffect } from "react";
import { usePhotoBlob } from "../lib/usePhotoBlob";

interface PhotoLightboxProps {
  photoIds: string[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}

export function PhotoLightbox({ photoIds, index, onIndexChange, onClose }: PhotoLightboxProps) {
  const currentPhotoId = photoIds[index];
  const { src, error } = usePhotoBlob(currentPhotoId, "full");

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
      {error && <p role="alert" style={{ color: "white" }}>failed to load photo</p>}
      {src && (
        // eslint-disable-next-line @next/next/no-img-element -- blob: URL from encrypted storage
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
