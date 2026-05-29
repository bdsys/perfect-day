import { render, act } from "@testing-library/react";
import { PhotoLightbox } from "../PhotoLightbox";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

beforeEach(() => {
  const blob = new Blob([new Uint8Array([0xff, 0xd8])], { type: "image/jpeg" });
  (api.photos.get as jest.Mock).mockResolvedValue(blob);
  jest.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake");
  jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
});

it("navigates with ArrowRight and closes on Escape", () => {
  const onClose = jest.fn();
  const onIndex = jest.fn();
  render(
    <PhotoLightbox
      photoIds={["a", "b", "c"]}
      index={1}
      onIndexChange={onIndex}
      onClose={onClose}
    />
  );

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
  });
  expect(onIndex).toHaveBeenCalledWith(2);

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft" }));
  });
  expect(onIndex).toHaveBeenCalledWith(0);

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
  expect(onClose).toHaveBeenCalled();
});
