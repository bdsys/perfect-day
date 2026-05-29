import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PhotoUploadButton } from "../PhotoUploadButton";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

it("calls onUploaded after full upload flow", async () => {
  (api.photos.requestUploadUrl as jest.Mock).mockResolvedValue({
    photo_id: "p1",
    upload_url: "https://example/upload",
    upload_key: "tmp/u/p1",
    expires_in: 900,
    required_headers: {},
  });
  (api.photos.uploadFile as jest.Mock).mockResolvedValue(undefined);
  (api.photos.finalize as jest.Mock).mockResolvedValue({
    id: "p1",
    mime_type: "image/jpeg",
    bytes: 100,
    taken_at: null,
    lat: null,
    lon: null,
    source: "upload",
    finalized_at: "2025-01-01T00:00:00Z",
    created_at: "2025-01-01T00:00:00Z",
    deleted_at: null,
    has_thumbnail: true,
  });

  const onUploaded = jest.fn();
  render(<PhotoUploadButton onUploaded={onUploaded} />);

  const file = new File([new Uint8Array(100)], "x.jpg", { type: "image/jpeg" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ id: "p1" })));
});
