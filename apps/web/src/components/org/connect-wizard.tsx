"use client";

import { useEffect, useRef, useState } from "react";

import {
  connectSnowflake as defaultConnect,
  ConnectConflictError,
  ConnectValidationError,
  type ConnectSnowflakeInput,
} from "../../lib/onboarding-api";
import { SNOWFLAKE_SETUP_SQL } from "./snowflake-setup-sql";

interface ConnectWizardProps {
  accessToken?: string | null;
  connect?: (input: ConnectSnowflakeInput, options: { accessToken?: string | null }) => Promise<string>;
  onConnected: (organizationId: string) => void;
}

const KEY_PAIR_DOCS =
  "https://docs.snowflake.com/en/user-guide/key-pair-auth#generate-the-private-keys";

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
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
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
            <Field id="orgName" label="Organization name" value={form.orgName} onChange={update("orgName")} required />
            <Field id="account" label="Account" value={form.account} onChange={update("account")} required />
            <Field id="user" label="User" value={form.user} onChange={update("user")} required />
            <Field id="role" label="Role" value={form.role} onChange={update("role")} required
              hint="The role must read the SNOWFLAKE.ACCOUNT_USAGE views." />
            <Field id="warehouse" label="Warehouse" value={form.warehouse} onChange={update("warehouse")} required />
            <Field id="database" label="Database (optional)" value={form.database ?? ""} onChange={update("database")} />
            <Field id="schema" label="Schema (optional)" value={form.schema ?? ""} onChange={update("schema")} />
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
                  <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
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
}

function Field({ id, label, value, onChange, required, type = "text", hint }: FieldProps) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-300" htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        className="mt-1 w-full rounded-md border border-hairline bg-canvas p-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
        value={value}
        onChange={onChange}
        required={required}
      />
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}
