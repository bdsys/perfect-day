import React from "react";
import { render, waitFor } from "@testing-library/react";
import { AuthProvider } from "../auth-context";
import { api, refreshAccessToken } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: { auth: { me: jest.fn() } },
  setAccessToken: jest.fn(),
  refreshAccessToken: jest.fn(),
}));

describe("AuthProvider", () => {
  it("auth-context's initial refresh uses the shared refresh", async () => {
    (refreshAccessToken as jest.Mock).mockResolvedValue(true);
    (api.auth.me as jest.Mock).mockResolvedValue({ id: "u1" });
    global.fetch = jest.fn() as unknown as typeof fetch;

    render(<AuthProvider>x</AuthProvider>);

    await waitFor(() => expect(api.auth.me).toHaveBeenCalled());
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
