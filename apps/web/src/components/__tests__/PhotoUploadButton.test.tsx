import { render, fireEvent, waitFor } from "@testing-library/react";
import { PhotoUploadButton } from "../PhotoUploadButton";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

function makeUploadMocks() {
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
}

it("calls onUploaded after full upload flow", async () => {
  makeUploadMocks();

  const onUploaded = jest.fn();
  const { getByRole, container } = render(<PhotoUploadButton onUploaded={onUploaded} />);

  // Button is present
  expect(getByRole("button", { name: /upload photo/i })).toBeInTheDocument();

  const file = new File([new Uint8Array(100)], "x.jpg", { type: "image/jpeg" });
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ id: "p1" })));
});

it("clicking the button programmatically triggers the file input", () => {
  makeUploadMocks();

  const clickSpy = jest.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
  const { getByRole } = render(<PhotoUploadButton onUploaded={jest.fn()} />);

  fireEvent.click(getByRole("button", { name: /upload photo/i }));
  expect(clickSpy).toHaveBeenCalledTimes(1);

  clickSpy.mockRestore();
});

it("renders custom label text when label prop is provided", () => {
  const { getByRole } = render(<PhotoUploadButton onUploaded={jest.fn()} label="Upload new" />);

  expect(getByRole("button", { name: /upload new/i })).toBeInTheDocument();
});
