"use client";

import { useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import { agree } from "../../lib/automated-savings-api";

type OptInGateProps = {
  orgId: string;
  roleName: string;
  onAgreed: () => void;
};

// Escapes a Snowflake identifier for safe interpolation into double-quoted
// SQL: a role containing a `"` can't break out of (or inject into) the
// rendered GRANT statement.
function quoteIdent(role: string): string {
  return `"${role.replace(/"/g, '""')}"`;
}

const REPO_URL = "https://github.com/greybeam-ai/greysight";

export function OptInGate({ orgId, roleName, onAgreed }: OptInGateProps) {
  const account = useAccountChrome();
  const [status, setStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [copied, setCopied] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const activeOrg = account?.organizations.find(
    (org) => org.id === (account.activeOrganizationId ?? orgId),
  );
  const isAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";

  const grantSql = `GRANT MANAGE WAREHOUSES ON ACCOUNT TO ROLE ${quoteIdent(roleName)};`;

  async function handleCopy() {
    await navigator.clipboard?.writeText(grantSql);
    setCopied(true);
    if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    copyTimeoutRef.current = setTimeout(() => setCopied(false), 2000);
  }

  async function handleAgree() {
    setStatus("submitting");
    try {
      await agree(orgId, { accessToken: account?.accessToken ?? null });
      onAgreed();
    } catch {
      setStatus("error");
    }
  }

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-100">Automated Savings</h2>
      <p className="mt-2 text-sm text-slate-400">
        Automated Savings monitors your Snowflake warehouses and lowers
        AUTO_SUSPEND during idle windows, then restores your configured
        default automatically — no manual tuning required.
      </p>
      <p className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-300">
        Experimental feature. Read more in{" "}
        <a
          className="underline hover:text-amber-200"
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
        >
          this repo
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
          <pre className="overflow-auto rounded-md border border-hairline bg-canvas p-4 text-xs text-slate-100">
            <code>{grantSql}</code>
          </pre>
        </div>
      </div>

      {status === "error" ? (
        <p className="mt-3 text-sm font-medium text-red-400" role="alert">
          Something went wrong. Please try again.
        </p>
      ) : null}

      <div className="mt-4">
        <button
          type="button"
          disabled={!isAdmin || status === "submitting"}
          aria-busy={status === "submitting"}
          onClick={handleAgree}
          className="rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Agree &amp; enable Automated Savings
        </button>
        {!isAdmin ? (
          <p className="mt-2 text-xs text-slate-500">
            Only owners and admins can enable Automated Savings for this
            organization.
          </p>
        ) : null}
      </div>
    </section>
  );
}
