import { formatDeadline } from "../deadline";

describe("formatDeadline", () => {
  it("shows the exact local moment", () => {
    expect(formatDeadline("2026-11-24T17:04:00Z")).toBe("Nov 24 at 9:04 AM");
  });
});
