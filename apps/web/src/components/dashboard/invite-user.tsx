"use client";

import { useEffect, useRef, useState } from "react";

import { useAccountChrome } from "../../lib/account-context";
import {
  inviteUser,
  InviteConflictError,
  InviteValidationError,
} from "../../lib/org-invitations-api";
import { isWorkEmail } from "../../lib/work-email";

const WORK_EMAIL_ERROR = "Please use your work email.";
const GENERIC_ERROR = "Something went wrong. Please try again.";

export default function InviteUser() {
  const account = useAccountChrome();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  if (!account) return null;
  const active =
    account.organizations.find((o) => o.id === account.activeOrganizationId) ??
    account.organizations[0];
  if (!active || (active.role !== "owner" && active.role !== "admin")) {
    return null;
  }

  const heading = active.accountLocator
    ? `Add user to ${active.name} (${active.accountLocator})`
    : `Add user to ${active.name}`;

  const { accessToken } = account;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const trimmed = email.trim();
    if (!isWorkEmail(trimmed)) {
      setError(WORK_EMAIL_ERROR);
      return;
    }
    setPending(true);
    try {
      const invited = await inviteUser(
        { organizationId: active.id, email: trimmed },
        { accessToken },
      );
      setSuccess(`Invited: ${invited} to ${active.name}`);
      setEmail("");
    } catch (err: unknown) {
      if (err instanceof InviteConflictError || err instanceof InviteValidationError) {
        setError(err.message || GENERIC_ERROR);
      } else {
        setError(GENERIC_ERROR);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Invite user"
        className="flex h-9 w-9 items-center justify-center rounded-md border border-hairline text-slate-300 hover:bg-white/5"
        onClick={() => setOpen((v) => !v)}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
        >
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M19 8v6M22 11h-6" />
        </svg>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={heading}
          className="absolute right-0 z-50 mt-2 w-80 rounded-md border border-hairline bg-surface p-3 shadow-lg"
        >
          <p className="mb-2 text-sm font-medium text-slate-200">{heading}</p>
          <form className="flex gap-2" onSubmit={submit}>
            <input
              autoComplete="email"
              type="email"
              required
              disabled={pending}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@work-email.com"
              className="flex-1 rounded-md border border-slate-600 bg-canvas px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-chart-purple focus:outline-none focus:ring-1 focus:ring-chart-purple"
            />
            <button
              type="submit"
              disabled={pending}
              className="shrink-0 rounded-md bg-chart-purple px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Inviting" : "Invite"}
            </button>
          </form>
          {error ? (
            <p className="mt-2 text-sm font-medium text-red-400" role="alert">
              {error}
            </p>
          ) : null}
          {success ? (
            <p className="mt-2 text-sm font-medium text-emerald-400" role="status">
              {success}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
