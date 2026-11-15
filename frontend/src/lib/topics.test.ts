import { describe, expect, it } from "vitest";
import { firstSentence } from "./topics";

describe("firstSentence", () => {
  it("keeps abbreviations inside the first sentence", () => {
    expect(firstSentence("Acme Inc. pays well. It also recycles.")).toBe("Acme Inc. pays well.");
  });
});
