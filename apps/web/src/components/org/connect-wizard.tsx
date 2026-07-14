"use client";

import { useEffect, useId, useRef, useState } from "react";

import {
  connectSnowflake as defaultConnect,
  ConnectConflictError,
  ConnectValidationError,
  type ConnectSnowflakeInput,
} from "../../lib/onboarding-api";
import Spinner from "../ui/spinner";
import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

interface ConnectWizardProps {
  accessToken?: string | null;
  connect?: (input: ConnectSnowflakeInput, options: { accessToken?: string | null }) => Promise<string>;
  onConnected: (organizationId: string) => void;
}

const KEY_PAIR_DOCS =
  "https://docs.snowflake.com/en/user-guide/key-pair-auth#generate-the-private-keys";

const ACCOUNT_LOCATOR_SQL =
  "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();";

const GREYSIGHT_OUTBOUND_IPS = [
  "162.220.232.250",
  "152.55.176.240",
  "162.220.232.252",
] as const;
const GREYSIGHT_OUTBOUND_IPS_TEXT = GREYSIGHT_OUTBOUND_IPS.join("\n");

const SQL_KEYWORDS = new Set([
  "SET", "USE", "ROLE", "CREATE", "USER", "ALTER", "WAREHOUSE", "GRANT", "IF",
  "NOT", "EXISTS", "IDENTIFIER", "TYPE", "SERVICE", "COMMENT", "DATABASE",
  "TO", "ON", "AND", "OR",
]);

// Captures single-quoted strings or word tokens; everything else is emitted as plain text.
const TOKEN_REGEX = /('[^']*')|([A-Za-z_]+)/g;

function highlightLine(line: string, lineKey: number) {
  // A line that begins (after whitespace) with `--` is a comment in full.
  if (line.trimStart().startsWith("--")) {
    return (
      <span key={lineKey} className="text-slate-500 italic">
        {line}
      </span>
    );
  }

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  TOKEN_REGEX.lastIndex = 0;
  let partKey = 0;

  while ((match = TOKEN_REGEX.exec(line)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(line.slice(lastIndex, match.index));
    }
    const [token, quoted, word] = match;
    if (quoted) {
      nodes.push(
        <span key={`${lineKey}-${partKey++}`} className="text-emerald-300">
          {token}
        </span>,
      );
    } else if (word && SQL_KEYWORDS.has(word.toUpperCase())) {
      nodes.push(
        <span key={`${lineKey}-${partKey++}`} className="text-sky-300">
          {token}
        </span>,
      );
    } else {
      nodes.push(token);
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }

  return <span key={lineKey}>{nodes}</span>;
}

function highlightSql(sql: string) {
  const lines = sql.split("\n");
  return lines.map((line, index) => (
    <span key={index}>
      {highlightLine(line, index)}
      {index < lines.length - 1 ? "\n" : null}
    </span>
  ));
}

export default function ConnectWizard({
  accessToken = null,
  connect = defaultConnect,
  onConnected,
}: ConnectWizardProps) {
  const [form, setForm] = useState<ConnectSnowflakeInput>({
    orgName: "", account: "", user: "", role: "", warehouse: "",
    database: "", schema: "", privateKeyPem: "", passphrase: "",
  });
  const [status, setStatus] = useState<"idle" | "submitting">("idle");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [accountCopied, setAccountCopied] = useState(false);
  const [ipsCopied, setIpsCopied] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const accountCopyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ipsCopyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const accountTooltipId = useId();

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
      if (accountCopyTimeoutRef.current) clearTimeout(accountCopyTimeoutRef.current);
      if (ipsCopyTimeoutRef.current) clearTimeout(ipsCopyTimeoutRef.current);
    };
  }, []);

  const update = (key: keyof ConnectSnowflakeInput) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((prev) => ({ ...prev, [key]: event.target.value }));

  async function handleCopy() {
    await navigator.clipboard?.writeText(SNOWFLAKE_SETUP_SQL);
    setCopied(true);
    if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    copyTimeoutRef.current = setTimeout(() => setCopied(false), 2000);
  }

  async function handleCopyAccountSql() {
    await navigator.clipboard?.writeText(ACCOUNT_LOCATOR_SQL);
    setAccountCopied(true);
    if (accountCopyTimeoutRef.current) clearTimeout(accountCopyTimeoutRef.current);
    accountCopyTimeoutRef.current = setTimeout(() => setAccountCopied(false), 2000);
  }

  async function handleCopyOutboundIps() {
    await navigator.clipboard?.writeText(GREYSIGHT_OUTBOUND_IPS_TEXT);
    setIpsCopied(true);
    if (ipsCopyTimeoutRef.current) clearTimeout(ipsCopyTimeoutRef.current);
    ipsCopyTimeoutRef.current = setTimeout(() => setIpsCopied(false), 2000);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setStatus("submitting");
    try {
      const organizationId = await connect(form, { accessToken });
      onConnected(organizationId);
    } catch (caught) {
      if (caught instanceof ConnectValidationError || caught instanceof ConnectConflictError) {
        setError(caught.message || "We couldn’t validate that Snowflake connection.");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setStatus("idle");
    }
  }

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6 shadow-sm">
      <div className="grid gap-8 md:grid-cols-2">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-slate-100">Connect your Snowflake account</h1>
          <p className="mt-1 text-sm text-slate-400">
            Greysight only reads metadata. Greysight never stores data, never reads tables, query results, or data. The key you grant is least-privilege and stored encrypted; disconnect anytime to delete it.
          </p>
          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <Field id="orgName" label="Organization name" placeholder="Acme Company" value={form.orgName} onChange={update("orgName")} required />
            <Field
              id="account"
              label="Account identifier"
              placeholder="abcde1-fgh234"
              value={form.account}
              onChange={update("account")}
              required
              labelAccessory={
                <span className="group relative inline-flex">
                  <button
                    type="button"
                    onClick={handleCopyAccountSql}
                    aria-describedby={accountTooltipId}
                    aria-label="How to find your account identifier — click to copy the SQL"
                    className="cursor-pointer rounded text-slate-400 hover:text-slate-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-slate-400"
                  >
                    &#x24D8;
                    <span
                      id={accountTooltipId}
                      role="tooltip"
                      className="pointer-events-auto absolute left-0 top-full z-10 mt-1 hidden w-max max-w-[min(20rem,80vw)] rounded bg-slate-800 px-3 py-2 text-left text-[11px] font-normal normal-case tracking-normal text-slate-200 shadow-lg group-hover:block group-focus-within:block"
                    >
                      <span className="block">Use your full account identifier. Run this in Snowflake to get it:</span>
                      <code className="mt-1 block whitespace-pre-wrap break-all font-mono text-emerald-300">
                        {ACCOUNT_LOCATOR_SQL}
                      </code>
                      <span className="mt-1 block text-slate-400">
                        {accountCopied ? "Copied!" : "Click to copy to clipboard"}
                      </span>
                    </span>
                  </button>
                </span>
              }
            />
            <Field id="user" label="User" placeholder="GREYSIGHT_USER" value={form.user} onChange={update("user")} required />
            <Field id="role" label="Role" placeholder="GREYSIGHT_ROLE" value={form.role} onChange={update("role")} required
              hint="The role must read the SNOWFLAKE.ACCOUNT_USAGE views." />
            <Field id="warehouse" label="Warehouse" placeholder="GREYSIGHT_WH" value={form.warehouse} onChange={update("warehouse")} required />
            <Field id="database" label="Database (optional)" placeholder="SNOWFLAKE" value={form.database ?? ""} onChange={update("database")} />
            <Field id="schema" label="Schema (optional)" placeholder="ACCOUNT_USAGE" value={form.schema ?? ""} onChange={update("schema")} />
            <div>
              <label className="block text-sm font-medium text-slate-300" htmlFor="privateKeyPem">
                Private key (PEM)
              </label>
              <textarea
                id="privateKeyPem"
                className="mt-1 h-32 w-full rounded-md border border-hairline bg-canvas p-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
                value={form.privateKeyPem}
                onChange={update("privateKeyPem")}
                required
              />
              <a className="text-xs text-slate-400 underline hover:text-slate-200" href={KEY_PAIR_DOCS} target="_blank" rel="noreferrer">
                How to generate a key pair
              </a>
            </div>
            <Field id="passphrase" label="Key passphrase (optional)" value={form.passphrase ?? ""} onChange={update("passphrase")} type="password" />
            {error ? (
              <p className="text-sm font-medium text-red-400" role="alert">{error}</p>
            ) : null}
            <button
              className="rounded-md bg-chart-purple px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              disabled={status === "submitting"}
              aria-busy={status === "submitting"}
              type="submit"
            >
              {status === "submitting" ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner />
                  Validating Snowflake connection…
                </span>
              ) : (
                "Test connection & save"
              )}
            </button>
          </form>
        </div>
        <aside className="flex flex-col gap-3">
          <p className="text-sm text-slate-400">
            Recommended: create a dedicated user + role for complete isolation. Replace the public key, then run:
          </p>
          <div className="relative flex-1 min-h-0">
            <button
              type="button"
              aria-label="Copy setup SQL"
              onClick={handleCopy}
              className="absolute right-2 top-2 rounded-md border border-hairline bg-surface px-2 py-1 text-xs font-medium text-slate-200 hover:bg-hairline"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
            <pre className="h-full overflow-auto rounded-md border border-hairline bg-canvas p-4 text-xs text-slate-100">
              <code>{highlightSql(SNOWFLAKE_SETUP_SQL)}</code>
            </pre>
          </div>
          <section className="rounded-md border border-hairline bg-canvas p-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-slate-200">
                  Network allowlist
                </h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  If your Snowflake account restricts inbound traffic, allow
                  Greysight’s outbound IPs before testing the connection.
                </p>
              </div>
              <button
                type="button"
                aria-label="Copy outbound IPs"
                onClick={handleCopyOutboundIps}
                className="shrink-0 rounded-md border border-hairline bg-surface px-2 py-1 text-xs font-medium text-slate-200 hover:bg-hairline"
              >
                {ipsCopied ? "Copied!" : "Copy"}
              </button>
              <span className="sr-only" role="status" aria-live="polite">
                {ipsCopied ? "Outbound IPs copied" : ""}
              </span>
            </div>
            <pre className="mt-3 overflow-x-auto rounded-md border border-hairline bg-surface p-3 text-xs text-slate-100">
              <code>{GREYSIGHT_OUTBOUND_IPS_TEXT}</code>
            </pre>
          </section>
        </aside>
      </div>
    </section>
  );
}

interface FieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  required?: boolean;
  type?: string;
  hint?: string;
  placeholder?: string;
  labelAccessory?: React.ReactNode;
}

function Field({ id, label, value, onChange, required, type = "text", hint, placeholder, labelAccessory }: FieldProps) {
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <label className="text-sm font-medium text-slate-300" htmlFor={id}>{label}</label>
        {labelAccessory}
      </div>
      <input
        id={id}
        type={type}
        placeholder={placeholder}
        className="mt-1 w-full rounded-md border border-hairline bg-canvas p-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
        value={value}
        onChange={onChange}
        required={required}
      />
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}
