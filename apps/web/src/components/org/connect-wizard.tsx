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

const VALIDATION_STEPS = [
  "Validating Snowflake connection…",
  "Checking SNOWFLAKE.ACCOUNT_USAGE access…",
  "Saving your connection…",
] as const;

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
  const [stepIndex, setStepIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const stepIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (stepIntervalRef.current) clearInterval(stepIntervalRef.current);
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
    setStepIndex(0);
    stepIntervalRef.current = setInterval(
      () => setStepIndex((i) => Math.min(i + 1, VALIDATION_STEPS.length - 1)),
      1500,
    );
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
    } finally {
      if (stepIntervalRef.current) clearInterval(stepIntervalRef.current);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold text-slate-950">Connect your Snowflake account</h1>
      <p className="mt-1 text-sm text-slate-600">
        Greybeam reads only Snowflake metadata. No query results or usage data leave your account.
      </p>
      <div className="mt-6 grid gap-8 md:grid-cols-2">
        <form className="space-y-4" onSubmit={handleSubmit}>
          <Field id="orgName" label="Organization name" value={form.orgName} onChange={update("orgName")} required />
          <Field id="account" label="Account" value={form.account} onChange={update("account")} required />
          <Field id="user" label="User" value={form.user} onChange={update("user")} required />
          <Field id="role" label="Role" value={form.role} onChange={update("role")} required
            hint="The role must read the SNOWFLAKE.ACCOUNT_USAGE views." />
          <Field id="warehouse" label="Warehouse" value={form.warehouse} onChange={update("warehouse")} required />
          <Field id="database" label="Database (optional)" value={form.database ?? ""} onChange={update("database")} />
          <Field id="schema" label="Schema (optional)" value={form.schema ?? ""} onChange={update("schema")} />
          <div>
            <label className="block text-sm font-medium text-slate-700" htmlFor="privateKeyPem">
              Private key (PEM)
            </label>
            <textarea
              id="privateKeyPem"
              className="mt-1 h-32 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
              value={form.privateKeyPem}
              onChange={update("privateKeyPem")}
              required
            />
            <a className="text-xs text-slate-500 underline" href={KEY_PAIR_DOCS} target="_blank" rel="noreferrer">
              How to generate a key pair
            </a>
          </div>
          <Field id="passphrase" label="Key passphrase (optional)" value={form.passphrase ?? ""} onChange={update("passphrase")} type="password" />
          {error ? (
            <p className="text-sm font-medium text-red-700" role="alert">{error}</p>
          ) : null}
          <button
            className="rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
            disabled={status === "submitting"}
            type="submit"
          >
            {status === "submitting" ? VALIDATION_STEPS[stepIndex] : "Test connection & save"}
          </button>
        </form>
        <aside className="flex flex-col gap-3">
          <p className="text-sm text-slate-700">
            Recommended: create a dedicated user + role for complete isolation. Replace the public key, then run:
          </p>
          <div className="relative flex-1 min-h-0">
            <button
              type="button"
              aria-label="Copy setup SQL"
              onClick={handleCopy}
              className="absolute right-2 top-2 rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
            <pre className="h-full overflow-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">
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
      <label className="block text-sm font-medium text-slate-700" htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm"
        value={value}
        onChange={onChange}
        required={required}
      />
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}
