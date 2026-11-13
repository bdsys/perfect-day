import { api, setAccessToken, type Photo } from "../api";

const MOCK_TOKEN = "test-token";

beforeEach(() => {
  setAccessToken(MOCK_TOKEN);
  jest.resetAllMocks();
});

describe("api.photos", () => {
  it("has all required methods", () => {
    expect(typeof api.photos.upload).toBe("function");
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

describe("API error parsing", () => {
  const respond = (status: number, body: unknown) =>
    (global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => body,
    }));

  it("surfaces the reason from the plain error envelope", async () => {
    respond(409, { error: { code: "http_error", message: "email_taken" } });
    await expect(api.auth.register("a@b.co", "password1")).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "email_taken",
      code: "email_taken",
    });
  });

  it("keeps structured tier_limit details", async () => {
    respond(403, { detail: { code: "tier_limit", details: { limit: 1, current: 1 } } });
    await expect(api.diaries.list()).rejects.toMatchObject({
      status: 403,
      code: "tier_limit",
      details: { limit: 1, current: 1 },
    });
  });

  it("reports wrong credentials on login without trying to refresh the session", async () => {
    const fetchMock = respond(401, { error: { code: "http_error", message: "invalid_credentials" } });
    await expect(api.auth.login("a@b.co", "wrong")).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("falls back to the status when the body is not JSON", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    });
    await expect(api.diaries.list()).rejects.toMatchObject({ status: 502, message: "HTTP 502" });
  });
});

describe("api.photos.upload", () => {
  class FakeXHR {
    static instances: FakeXHR[] = [];
    headers: Record<string, string> = {};
    withCredentials = false;
    status = 0;
    responseText = "";
    sent: unknown;
    upload = {} as { onprogress?: unknown };
    onload?: () => void;
    onerror?: () => void;
    onabort?: () => void;
    ontimeout?: () => void;
    open = jest.fn();
    setRequestHeader(k: string, v: string) { this.headers[k] = v; }
    send(body: unknown) { this.sent = body; FakeXHR.instances.push(this); }
    respond(status: number, body: unknown) {
      this.status = status; this.responseText = JSON.stringify(body); this.onload?.();
    }
  }
  beforeEach(() => {
    FakeXHR.instances = [];
    (global as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = FakeXHR;
  });

  it("sends the raw file with bearer token, and retries once after a 401 refresh", async () => {
    const file = new File(["abc"], "a.heic", { type: "image/heic" });
    global.fetch = jest.fn().mockResolvedValueOnce({
      ok: true, status: 200, json: async () => ({ access_token: "new-token" }),
    } as unknown as Response);
    const p = api.photos.upload(file);
    const first = FakeXHR.instances[0];
    expect(first.sent).toBe(file);
    expect(first.withCredentials).toBe(true);
    expect(first.headers["Content-Type"]).toBe("image/heic");
    expect(first.headers["Authorization"]).toBe(`Bearer ${MOCK_TOKEN}`);
    first.respond(401, {});
    await new Promise((r) => setTimeout(r, 0));
    const second = FakeXHR.instances[1];
    expect(second.headers["Authorization"]).toBe("Bearer new-token");
    second.respond(201, { id: "p1" });
    await expect(p).resolves.toEqual({ id: "p1" });
  });

  it("rejects with the API error code on 413", async () => {
    const p = api.photos.upload(new File(["a"], "a.jpg", { type: "image/jpeg" }));
    FakeXHR.instances[0].respond(413, { error: { code: "http_error", message: "size_exceeds_limit" } });
    await expect(p).rejects.toMatchObject({ status: 413, code: "size_exceeds_limit" });
  });

  it("settles when the XHR is aborted or times out", async () => {
    const f = new File(["a"], "a.jpg", { type: "image/jpeg" });
    const p1 = api.photos.upload(f);
    FakeXHR.instances[0].onabort?.();
    await expect(p1).rejects.toThrow("upload network error");
    const p2 = api.photos.upload(f);
    FakeXHR.instances[1].ontimeout?.();
    await expect(p2).rejects.toThrow("upload network error");
  });
});
