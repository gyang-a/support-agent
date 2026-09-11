import { expect, it, vi } from "vitest";
import { newRunId } from "./uuid";

it("creates valid unique UUIDs on HTTP origins without randomUUID", () => {
  const getRandomValues = crypto.getRandomValues.bind(crypto);
  vi.stubGlobal("crypto", { getRandomValues });
  try {
    const ids = new Set(Array.from({ length: 20 }, newRunId));
    expect(ids.size).toBe(20);
    for (const id of ids)
      expect(id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
  } finally {
    vi.unstubAllGlobals();
  }
});
