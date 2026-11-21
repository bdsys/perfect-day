import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "@/lib/auth-context";
import { api, refreshAccessToken, setPendingDeletionHandler } from "@/lib/api";
import { navigateTo } from "@/lib/navigate";

jest.mock("@/lib/api", () => ({
  api: {
    auth: { me: jest.fn(), avatar: jest.fn(), restoreAccount: jest.fn(), logout: jest.fn() },
  },
  setAccessToken: jest.fn(),
  refreshAccessToken: jest.fn(),
  setPendingDeletionHandler: jest.fn(),
  ApiError: jest.requireActual("@/lib/api").ApiError,
}));
jest.mock("@/lib/navigate", () => ({ navigateTo: jest.fn() }));

const me = api.auth.me as jest.Mock;
const restoreAccount = api.auth.restoreAccount as jest.Mock;
const logoutApi = api.auth.logout as jest.Mock;

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
const pending = () => profile({ deletion_scheduled_for: "2026-11-24T17:04:00Z" });

const mount = () =>
  render(
    <AuthProvider>
      <div>the app</div>
    </AuthProvider>,
  );

beforeEach(() => {
  jest.resetAllMocks();
  (refreshAccessToken as jest.Mock).mockResolvedValue(true);
});

describe("restore screen", () => {
  it("shows the deadline from /me instead of the app", async () => {
    me.mockResolvedValue(pending());
    mount();
    expect(
      await screen.findByText("Your account will be deleted on Nov 24 at 9:04 AM."),
    ).toBeInTheDocument();
    expect(screen.queryByText("the app")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore my account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("a 403 from any call shows the screen, fetching the deadline", async () => {
    me.mockResolvedValueOnce(profile());
    mount();
    expect(await screen.findByText("the app")).toBeInTheDocument();
    me.mockResolvedValue(pending());
    const handler = (setPendingDeletionHandler as jest.Mock).mock.calls.at(-1)![0];
    act(() => handler());
    expect(screen.getByText("Your account is scheduled for deletion.")).toBeInTheDocument();
    expect(
      await screen.findByText("Your account will be deleted on Nov 24 at 9:04 AM."),
    ).toBeInTheDocument();
    expect(screen.queryByText("the app")).not.toBeInTheDocument();
  });

  it("Restore my account restores and shows the app", async () => {
    me.mockResolvedValueOnce(pending());
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Restore my account" }));
    me.mockResolvedValue(profile());
    restoreAccount.mockResolvedValue(undefined);
    await waitFor(() => expect(screen.getByText("the app")).toBeInTheDocument());
    expect(restoreAccount).toHaveBeenCalledTimes(1);
  });

  it("a failed restore says so and keeps the screen", async () => {
    me.mockResolvedValue(pending());
    restoreAccount.mockRejectedValue(new Error("boom"));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Restore my account" }));
    expect(await screen.findByText("Couldn't restore your account. Try again.")).toBeInTheDocument();
    expect(screen.queryByText("the app")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore my account" })).toBeEnabled();
  });

  it("row 54: a restore refused because the account is gone says so, not try again", async () => {
    const { ApiError } = jest.requireActual("@/lib/api");
    me.mockResolvedValue(pending());
    restoreAccount.mockRejectedValue(new ApiError(401, "account_unavailable", "account_unavailable"));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Restore my account" }));
    expect(await screen.findByText("This account is no longer available.")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't restore your account. Try again.")).not.toBeInTheDocument();
    // Nothing left to restore: no second click that would say "Try again" (final review).
    expect(screen.queryByRole("button", { name: "Restore my account" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });

  it("Sign out signs out and goes to /login", async () => {
    me.mockResolvedValue(pending());
    logoutApi.mockResolvedValue(undefined);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login"));
    expect(logoutApi).toHaveBeenCalledTimes(1);
  });
});
