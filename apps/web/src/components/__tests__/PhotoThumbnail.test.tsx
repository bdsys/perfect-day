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
