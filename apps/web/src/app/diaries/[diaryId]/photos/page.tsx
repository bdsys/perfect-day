"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, Photo } from "@/lib/api";
import { PhotoThumbnail } from "@/components/PhotoThumbnail";
import { PhotoUploadButton } from "@/components/PhotoUploadButton";
import { PhotoLightbox } from "@/components/PhotoLightbox";

export default function DiaryPhotosPage() {
  const { diaryId } = useParams<{ diaryId: string }>();
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  useEffect(() => {
    api.photos.listForDiary(diaryId).then(setPhotos);
  }, [diaryId]);

  async function handleUploaded(p: Photo) {
    await api.photos.attachToDiary(diaryId, p.id);
    const refreshed = await api.photos.listForDiary(diaryId);
    setPhotos(refreshed);
  }

  return (
    <main>
      <h1>Photo library</h1>
      <PhotoUploadButton onUploaded={handleUploaded} />
      <ul className="grid" style={{ listStyle: "none", padding: 0, display: "flex", flexWrap: "wrap", gap: "8px" }}>
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
  );
}
