// Port of the backend `_format_currency` in
// apps/api/app/services/dashboard_view_builder.py (source of truth). Keep these
// tables and rules in sync; a future DuckDB refactor retires this duplication.
// Used only to re-label a recomputed KPI total for an in-memory filtered subset.

const CURRENCY_SYMBOL_PREFIXES: Record<string, string> = {
  EUR: "€", GBP: "£", JPY: "¥", KRW: "₩", CAD: "CA$", AUD: "A$", NZD: "NZ$",
  MXN: "MX$", INR: "₹", CNY: "CN¥", HKD: "HK$", BRL: "R$", ILS: "₪",
  TWD: "NT$", PHP: "₱",
};
const CURRENCY_CODE_PREFIXES = new Set([
  "CHF", "CZK", "DKK", "HUF", "IDR", "MYR", "NOK", "PLN", "SEK", "SGD",
  "THB", "TRY", "ZAR",
]);
const CURRENCY_CODE_SEPARATOR = " ";
const CURRENCY_COMPACT_DECIMAL_CODES = new Set(["HUF", "IDR", "JPY", "KRW"]);

// Group digits with a fixed 2-decimal, half-up rounding — matches Python's
// f"{value:,.2f}" with ROUND_HALF_UP.
function formatFixed(value: number): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// Compact variant used for HUF/IDR/JPY/KRW: format fixed, then strip trailing
// zeros and a dangling decimal point (mirrors backend `_format_compact_amount`).
function formatCompact(value: number): string {
  return formatFixed(value).replace(/\.?0+$/, "");
}

function formatAmount(value: number, currency: string): string {
  return CURRENCY_COMPACT_DECIMAL_CODES.has(currency)
    ? formatCompact(value)
    : formatFixed(value);
}

export function formatCurrency(value: number, currency: string | null): string {
  if (!Number.isFinite(value)) {
    throw new Error("Dashboard currency value must be finite.");
  }
  const resolved = currency || "USD";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);

  if (resolved === "USD") {
    return `${sign}$${formatFixed(abs)}`;
  }
  // Object.hasOwn, not `resolved in CURRENCY_SYMBOL_PREFIXES` — `in` walks the
  // prototype chain, so a currency string like "toString" or "constructor"
  // would otherwise be truthy and render function text instead of falling
  // through to the unknown-currency branch below.
  if (Object.hasOwn(CURRENCY_SYMBOL_PREFIXES, resolved)) {
    return `${sign}${CURRENCY_SYMBOL_PREFIXES[resolved]}${formatAmount(abs, resolved)}`;
  }
  if (CURRENCY_CODE_PREFIXES.has(resolved)) {
    return `${sign}${resolved}${CURRENCY_CODE_SEPARATOR}${formatAmount(abs, resolved)}`;
  }
  return `${formatFixed(value)} ${resolved}`;
}
