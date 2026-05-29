import { api } from "../api";

describe("api.photos", () => {
  it("has all required methods", () => {
    expect(typeof api.photos.requestUploadUrl).toBe("function");
    expect(typeof api.photos.uploadFile).toBe("function");
    expect(typeof api.photos.finalize).toBe("function");
    expect(typeof api.photos.get).toBe("function");
    expect(typeof api.photos.delete).toBe("function");
    expect(typeof api.photos.attachToDiary).toBe("function");
    expect(typeof api.photos.attachToEntry).toBe("function");
    expect(typeof api.photos.detachFromEntry).toBe("function");
    expect(typeof api.photos.listForDiary).toBe("function");
  });
});
