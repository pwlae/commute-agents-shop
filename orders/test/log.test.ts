import { describe, expect, it } from "vitest";
import { createLogger, logfmt } from "../src/log.js";

describe("logfmt", () => {
  it("quotes values with spaces, quotes or equals", () => {
    expect(logfmt({ a: "x", b: "two words", c: 'say "hi"', d: "k=v", e: 3 }))
      .toBe('a=x b="two words" c="say \\"hi\\"" d="k=v" e=3');
  });

  it("writes time, level, service and msg first", () => {
    const lines: string[] = [];
    const log = createLogger("orders", "info", (l) => lines.push(l));
    log.debug("hidden");
    log.warn("charge call failed", { err: new Error("timeout after 2000 ms") });
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatch(
      /^time=\S+Z level=warn service=orders msg="charge call failed" err="timeout after 2000 ms"$/,
    );
  });
});
