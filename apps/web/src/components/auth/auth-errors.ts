// Shared, user-facing auth error strings and mapping. Kept intentionally small
// and free of provider wording so both the login form and the code entry view
// surface identical, non-leaky messages.

export const GENERIC_ERROR = "Something went wrong. Please try again.";
export const RATE_LIMIT_ERROR =
  "Too many requests. Please wait a moment and try again.";

// Never surface provider/internal wording verbatim. Recognize the one
// user-actionable case (rate limiting) and fall back to the generic message for
// everything else.
export function friendlyAuthError(message?: string | null): string {
  if (message && /rate limit|too many/i.test(message)) {
    return RATE_LIMIT_ERROR;
  }
  return GENERIC_ERROR;
}
