import { describe, expect, it } from "vitest";
import { formatCurrency } from "./currency-format";

describe("formatCurrency", () => {
  it("formats USD with a dollar sign and two decimals", () => {
    expect(formatCurrency(8494.99, "USD")).toBe("$8,494.99");
    expect(formatCurrency(8494.985, "USD")).toBe("$8,494.99"); // round half up
  });
  it("defaults null/empty currency to USD", () => {
    expect(formatCurrency(1000, null)).toBe("$1,000.00");
    expect(formatCurrency(1000, "")).toBe("$1,000.00");
  });
  it("prefixes symbol currencies", () => {
    expect(formatCurrency(1234.5, "EUR")).toBe("€1,234.50");
    expect(formatCurrency(1234.5, "GBP")).toBe("£1,234.50");
  });
  it("prefixes code currencies with a non-breaking space", () => {
    expect(formatCurrency(1234.5, "CHF")).toBe("CHF 1,234.50");
  });
  it("uses compact decimals for HUF/IDR/JPY/KRW", () => {
    expect(formatCurrency(1200, "JPY")).toBe("¥1,200"); // trailing .00 stripped
    expect(formatCurrency(1200.5, "JPY")).toBe("¥1,200.5");
  });
  it("falls back to trailing currency code for unknown currencies", () => {
    expect(formatCurrency(1000, "XYZ")).toBe("1,000.00 XYZ");
  });
  it("does not treat a prototype-chain property name as a known currency", () => {
    // Guards against `resolved in CURRENCY_SYMBOL_PREFIXES` (which would walk
    // the prototype chain and match "toString"/"constructor"/etc, rendering
    // function text instead of falling through to the unknown-currency label).
    expect(formatCurrency(1000, "toString")).toBe("1,000.00 toString");
  });
  it("handles negatives and zero", () => {
    expect(formatCurrency(-42.5, "USD")).toBe("-$42.50");
    expect(formatCurrency(0, "USD")).toBe("$0.00");
  });
});
