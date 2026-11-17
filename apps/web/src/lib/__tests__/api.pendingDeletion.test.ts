import { api, setAccessToken, setPendingDeletionHandler, ApiError } from "../api";

const pending = () => ({
  ok: false,
  status: 403,
  json: async () => ({ error: { code: "http_error", message: "account_pending_deletion" } }),
});

beforeEach(() => {
  setAccessToken("tok");
  setPendingDeletionHandler(null);
});

describe("pending deletion signal", () => {
  it("a 403 account_pending_deletion calls the handler and does not refresh", async () => {
    const handler = jest.fn();
    setPendingDeletionHandler(handler);
    const fetchMock = jest.fn().mockResolvedValue(pending());
    global.fetch = fetchMock as unknown as typeof fetch;

    await expect(api.auth.myPlan()).rejects.toMatchObject({
      status: 403,
      code: "account_pending_deletion",
    });
    await expect(api.auth.myPlan()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(2);
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/v1/auth/refresh"))).toBe(false);
  });

  it("the blob path signals too", async () => {
    const handler = jest.fn();
    setPendingDeletionHandler(handler);
    global.fetch = jest.fn().mockResolvedValue(pending()) as unknown as typeof fetch;
    await expect(api.auth.avatar()).rejects.toMatchObject({ code: "account_pending_deletion" });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("other 403s do not call the handler", async () => {
    const handler = jest.fn();
    setPendingDeletionHandler(handler);
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: { code: "http_error", message: "forbidden" } }),
    }) as unknown as typeof fetch;
    await expect(api.auth.myPlan()).rejects.toMatchObject({ status: 403, code: "forbidden" });
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("avatar upload", () => {
  it("PUTs the raw body with the file's type", async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: "u" }) });
    global.fetch = fetchMock as unknown as typeof fetch;
    const file = new Blob(["abc"], { type: "image/png" });
    await api.auth.uploadAvatar(file);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/auth/me/avatar");
    expect(init.method).toBe("PUT");
    expect(init.body).toBe(file);
    expect(init.headers["Content-Type"]).toBe("image/png");
  });
});
