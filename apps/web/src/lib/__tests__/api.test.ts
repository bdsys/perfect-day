import { api, setAccessToken, type Photo } from "../api";

const MOCK_TOKEN = "test-token";

beforeEach(() => {
  setAccessToken(MOCK_TOKEN);
  jest.resetAllMocks();
});

describe("api.photos", () => {
  it("has all required methods", () => {
    expect(typeof api.photos.requestUploadUrl).toBe("function");
    expect(typeof api.photos.uploadFile).toBe("function");
    expect(typeof api.photos.finalize).toBe("function");
    expect(typeof api.photos.get).toBe("function");
    expect(typeof api.photos.delete).toBe("function");
    expect(typeof api.photos.listForUser).toBe("function");
    expect(typeof api.photos.attachToEntry).toBe("function");
    expect(typeof api.photos.detachFromEntry).toBe("function");
  });

  it("listForUser makes GET /v1/photos with bearer token and returns Photo[]", async () => {
    const mockPhotos: Photo[] = [
      {
        id: "photo-1",
        mime_type: "image/jpeg",
        bytes: 1024,
        taken_at: "2024-01-01T00:00:00Z",
        lat: null,
        lon: null,
        source: "upload",
        finalized_at: "2024-01-01T00:01:00Z",
        created_at: "2024-01-01T00:00:00Z",
        deleted_at: null,
        has_thumbnail: true,
      },
    ];

    global.fetch = jest.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => mockPhotos,
    } as unknown as Response);

    const result = await api.photos.listForUser();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, options] = (global.fetch as jest.Mock).mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/photos$/);
    expect((options.headers as Record<string, string>)["Authorization"]).toBe(
      `Bearer ${MOCK_TOKEN}`
    );
    expect(result).toEqual(mockPhotos);
  });
});
