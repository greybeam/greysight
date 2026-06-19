// Consumer email providers we reject so sign-ups use a work address. Blocklist
// (not allowlist): block known free providers rather than trying to prove a
// domain belongs to a real company — good enough for a lead-magnet gate. This is
// a client-side check only; server-side enforcement is a tracked follow-up.
export const FREE_EMAIL_DOMAINS: ReadonlySet<string> = new Set([
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.uk",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "msn.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "aol.com",
  "proton.me",
  "protonmail.com",
  "gmx.com",
  "mail.com",
  "zoho.com",
  "yandex.com",
  "qq.com",
  "163.com",
]);

// True only for a syntactically plausible email whose domain is not a known free
// provider. The pattern requires a non-empty local part, exactly one "@", and a
// dotted domain whose labels are each non-empty — so malformed forms like
// "a@.com", "a@b.", and "a@b..com" are rejected, not just the no-"@" cases.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/;

export function isWorkEmail(email: string): boolean {
  const normalized = email.trim().toLowerCase();
  if (!EMAIL_PATTERN.test(normalized)) {
    return false;
  }
  const domain = normalized.slice(normalized.indexOf("@") + 1);
  return !FREE_EMAIL_DOMAINS.has(domain);
}
