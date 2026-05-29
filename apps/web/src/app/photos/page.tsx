"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Photo } from "@/lib/api";
import { PhotoThumbnail } from "@/components/PhotoThumbnail";
import { PhotoUploadButton } from "@/components/PhotoUploadButton";
import { PhotoLightbox } from "@/components/PhotoLightbox";

export default function UserPhotosPage() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.photos
      .listForUser()
      .then((p) => {
        if (!cancelled) setPhotos(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load photos");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  async function handleUploaded(_p: Photo) {
    try {
      const refreshed = await api.photos.listForUser();
      setPhotos(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh photos");
    }
  }

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href="/diaries" className="nav-brand">
            ← Diaries
          </Link>
        </div>
      </nav>
      <main className="container" style={{ paddingTop: "1.5rem" }}>
        <h1>Photo library</h1>
        {error && <p role="alert">{error}</p>}
        <PhotoUploadButton onUploaded={handleUploaded} />
        <ul className="photo-grid">
          {photos.map((p, i) => (
            <li key={p.id}>
              <PhotoThumbnail
                photoId={p.id}
                alt=""
                onClick={() => setOpenIndex(i)}
                className="thumbnail"
              />
            </li>
          ))}
        </ul>
        {openIndex !== null && (
          <PhotoLightbox
            photoIds={photos.map((p) => p.id)}
            index={openIndex}
            onIndexChange={setOpenIndex}
            onClose={() => setOpenIndex(null)}
          />
        )}
      </main>
    </>
  );
}
