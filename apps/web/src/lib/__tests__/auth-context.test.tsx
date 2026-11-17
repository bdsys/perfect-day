import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "../auth-context";
import { api, refreshAccessToken, setPendingDeletionHandler } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: {
    auth: { me: jest.fn(), avatar: jest.fn(), restoreAccount: jest.fn() },
  },
  setAccessToken: jest.fn(),
  refreshAccessToken: jest.fn(),
  setPendingDeletionHandler: jest.fn(),
}));

const me = api.auth.me as jest.Mock;
const avatar = api.auth.avatar as jest.Mock;
const restoreAccount = api.auth.restoreAccount as jest.Mock;

const profile = (over: Record<string, unknown> = {}) => ({
  id: "u1",
  email: "a@b.c",
  display_name: null,
  subscription_tier: "free",
  is_admin: false,
  email_verified_at: null,
  temperature_unit: "F",
  avatar: { kind: "initial" },
  deletion_scheduled_for: null,
  ...over,
});

let ctx: ReturnType<typeof useAuth>;
function Probe() {
  ctx = useAuth();
  return null;
}
const mount = () =>
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );

let n = 0;
beforeEach(() => {
  jest.resetAllMocks();
  n = 0;
  (refreshAccessToken as jest.Mock).mockResolvedValue(true);
  URL.createObjectURL = jest.fn(() => `blob:${++n}`);
  URL.revokeObjectURL = jest.fn();
});

describe("AuthProvider", () => {
  it("auth-context's initial refresh uses the shared refresh", async () => {
    me.mockResolvedValue(profile());
    global.fetch = jest.fn() as unknown as typeof fetch;
    render(<AuthProvider>x</AuthProvider>);
    await waitFor(() => expect(api.auth.me).toHaveBeenCalled());
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("pendingDeletion follows /me deletion_scheduled_for", async () => {
    me.mockResolvedValue(profile({ deletion_scheduled_for: "2026-11-24T17:04:00Z" }));
    mount();
    await waitFor(() => expect(ctx.loading).toBe(false));
    expect(ctx.pendingDeletion).toBe(true);
  });

  it("the handler flips pendingDeletion", async () => {
    me.mockResolvedValue(profile());
    mount();
    await waitFor(() => expect(ctx.loading).toBe(false));
    expect(ctx.pendingDeletion).toBe(false);
    const handler = (setPendingDeletionHandler as jest.Mock).mock.calls.at(-1)![0];
    act(() => handler());
    expect(ctx.pendingDeletion).toBe(true);
  });

  it("restore clears pendingDeletion and re-reads /me", async () => {
    me.mockResolvedValueOnce(profile({ deletion_scheduled_for: "2026-11-24T17:04:00Z" }));
    me.mockResolvedValue(profile());
    restoreAccount.mockResolvedValue(undefined);
    mount();
    await waitFor(() => expect(ctx.pendingDeletion).toBe(true));
    await act(async () => {
      await ctx.restore();
    });
    expect(restoreAccount).toHaveBeenCalled();
    expect(me).toHaveBeenCalledTimes(2);
    expect(ctx.pendingDeletion).toBe(false);
  });

  it("avatarUrl is an object URL only for a photo avatar, revoking the old one", async () => {
    me.mockResolvedValueOnce(profile({ avatar: { kind: "photo" } }));
    avatar.mockResolvedValue(new Blob(["x"]));
    const view = mount();
    await waitFor(() => expect(ctx.avatarUrl).toBe("blob:1"));

    me.mockResolvedValueOnce(profile({ avatar: { kind: "photo" } }));
    await act(async () => {
      await ctx.refreshUser();
    });
    expect(ctx.avatarUrl).toBe("blob:2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:1");

    me.mockResolvedValueOnce(profile({ avatar: { kind: "icon", name: "cat" } }));
    await act(async () => {
      await ctx.refreshUser();
    });
    expect(ctx.avatarUrl).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:2");

    me.mockResolvedValueOnce(profile({ avatar: { kind: "photo" } }));
    await act(async () => {
      await ctx.refreshUser();
    });
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:3");
  });
});
