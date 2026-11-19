import { render, screen, waitFor } from "@testing-library/react";
import { PhotoThumbnail } from "../PhotoThumbnail";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

it("fetches blob and renders img with src", async () => {
  const blob = new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" });
  (api.photos.get as jest.Mock).mockResolvedValue(blob);

  // Mock URL.createObjectURL
  const fakeUrl = "blob:fake-url";
  jest.spyOn(URL, "createObjectURL").mockReturnValue(fakeUrl);
  const revokeSpy = jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

  const { unmount } = render(<PhotoThumbnail photoId="test-id" alt="test photo" />);
  await waitFor(() => expect(screen.getByRole("img")).toHaveAttribute("src", fakeUrl));

  unmount();
  expect(revokeSpy).toHaveBeenCalled();
});

it("row 61: switching photos shows the loading box, never the previous photo's revoked URL", async () => {
  (api.photos.get as jest.Mock).mockReset()
    .mockResolvedValueOnce(new Blob(["a"]))
    .mockReturnValueOnce(new Promise(() => {}));
  jest.spyOn(URL, "createObjectURL").mockReturnValue("blob:first");
  jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const { rerender, container } = render(<PhotoThumbnail photoId="a" alt="a" />);
  await waitFor(() => expect(screen.getByRole("img")).toHaveAttribute("src", "blob:first"));
  rerender(<PhotoThumbnail photoId="b" alt="b" />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
});
