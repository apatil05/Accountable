import { describe, expect, it } from "vitest";
import { progressFor } from "./useResearch";

describe("progressFor", () => {
  it.each([
    ["started", "working"],
    ["running", "working"],
    ["busy", "working"],
    ["capped", "capped"],
    ["failed", "failed"],
    ["unavailable", "unavailable"],
    ["ready", "idle"],
    ["something-new", "idle"],
  ])("%s -> %s", (status, expected) => {
    expect(progressFor(status)).toBe(expected);
  });
});
