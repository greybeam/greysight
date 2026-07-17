"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import { agree } from "../../lib/automated-savings-api";
import {
  useQueryIdentity,
  type QueryIdentitySnapshot,
} from "../../lib/query-identity";

type OptInGateProps = {
  orgId: string;
  roleName: string;
  onAgreed: (captured: QueryIdentitySnapshot) => void;
};

// Escapes a Snowflake identifier for safe interpolation into double-quoted
// SQL: a role containing a `"` can't break out of (or inject into) the
// rendered GRANT statement.
export function quoteIdent(role: string): string {
  return `"${role.replace(/"/g, '""')}"`;
}

// The API's StatusResponse/CheckAccessResponse carry `role_name`, but it's a
// best-effort lookup: it's null when Snowflake isn't configured for the org
// or resolution fails for any other reason. Callers fall back to this
// clearly-named placeholder so the GRANT SQL is still renderable (and
// obviously wrong if actually run) when no real role is available.
export const UNKNOWN_ROLE_PLACEHOLDER = "<YOUR_SNOWFLAKE_ROLE>";

// A blank/whitespace-only role name (e.g. an API quirk or a caller passing
// "" instead of null) must never reach the GRANT SQL as an empty-quoted
// identifier (`TO ROLE ""`) — normalize it to null so callers fall back to
// UNKNOWN_ROLE_PLACEHOLDER instead.
export function normalizeRoleName(
  roleName: string | null | undefined,
): string | null {
  return roleName?.trim() ? roleName : null;
}

// Builds the GRANT MANAGE WAREHOUSES statement for a (possibly missing) role,
// falling back to the placeholder when no real role is available. The single
// source of truth shared by the opt-in gate and the shell's grant-missing banner.
export function buildGrantSql(roleName: string | null | undefined): string {
  const role = normalizeRoleName(roleName) ?? UNKNOWN_ROLE_PLACEHOLDER;
  return `GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE ${quoteIdent(role)};`;
}

const REPO_URL =
  "https://github.com/greybeam/greysight/blob/main/docs/automated-savings-how-it-works.md";

export function OptInGate({ orgId, roleName, onAgreed }: OptInGateProps) {
  const account = useAccountChrome();
  const queryIdentity = useQueryIdentity();
  const [status, setStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [copied, setCopied] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const activeOrg = account?.organizations.find(
    (org) => org.id === (account.activeOrganizationId ?? orgId),
  );
  const isAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";

  const grantSql = buildGrantSql(roleName);

  async function handleCopy() {
    await navigator.clipboard?.writeText(grantSql);
    setCopied(true);
    if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    copyTimeoutRef.current = setTimeout(() => setCopied(false), 2000);
  }

  async function handleAgree() {
    if (!isAdmin || status === "submitting") return;
    // Capture the query identity at the moment agreement starts so the
    // completion flow can be dropped if the org/account switches while the
    // request is in flight (see handleAgreementComplete in the shell).
    const captured = queryIdentity.capture();
    setStatus("submitting");
    try {
      await agree(orgId, { accessToken: account?.accessToken ?? null });
      onAgreed(captured);
      // For the current identity, onAgreed refetches status and this gate
      // unmounts once agreed renders, so the lingering "submitting" is moot. But
      // if the org/account switched to another still-unagreed workspace while the
      // request was in flight, this same gate stays mounted for the NEW org —
      // reset to idle so its button is usable instead of stuck disabled forever.
      if (!queryIdentity.isCurrent(captured)) {
        setStatus("idle");
      }
    } catch {
      setStatus("error");
    }
  }

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2
          className="text-lg font-semibold text-slate-100"
          id="automated-savings-opt-in-title"
        >
          Auto Savings
        </h2>
        <span className="rounded-full border border-amber-500/50 px-2 py-0.5 text-[11px] font-medium text-amber-300">
          Experimental
        </span>
      </div>
      <p className="mt-2 text-sm text-slate-400">
        Auto Savings polls your Snowflake warehouses and requests safe
        suspension after the billing floor when enrolled warehouses are idle.{" "}
        <a
          className="text-slate-300 underline decoration-slate-600 underline-offset-2 hover:text-slate-100"
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
        >
          Learn more about how Auto Savings works
        </a>
        .
      </p>

      <div className="mt-4">
        <p className="text-sm text-slate-400">
          Grant the Greysight role permission to manage warehouses:
        </p>
        <div className="relative mt-2">
          <button
            type="button"
            aria-label="Copy GRANT SQL"
            onClick={handleCopy}
            className="absolute right-2 top-2 rounded-md border border-hairline bg-surface px-2 py-1 text-xs font-medium text-slate-200 hover:bg-hairline"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
          <pre className="overflow-x-auto rounded-md border border-hairline bg-canvas py-3 pl-3 pr-20 text-xs text-slate-100">
            <code>{grantSql}</code>
          </pre>
        </div>
      </div>

      {status === "error" ? (
        <p className="mt-3 text-sm font-medium text-red-400" role="alert">
          Something went wrong. Please try again.
        </p>
      ) : null}

      <div className="mt-4 border-t border-hairline pt-4">
        <p className="text-xs leading-5 text-slate-500">
          By continuing, you acknowledge that Auto Savings is experimental
          and authorize Greysight to request safe Snowflake suspension for
          warehouses you choose to enroll. You can disable the feature at any
          time.
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!isAdmin || status === "submitting"}
            aria-busy={status === "submitting"}
            onClick={handleAgree}
            className="rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Agree &amp; continue
          </button>
          <Link
            className="inline-flex rounded-md border border-hairline px-4 py-2 text-sm font-medium text-slate-300 hover:bg-hairline hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chart-purple"
            href="/dashboard"
          >
            Back to Greysight home
          </Link>
        </div>

        {!isAdmin ? (
          <p className="mt-3 text-xs text-slate-500">
            Only owners and admins can enable Auto Savings for this
            organization.
          </p>
        ) : null}
      </div>
    </section>
  );
}
