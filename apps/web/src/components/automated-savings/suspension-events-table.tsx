"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from "@tremor/react";

import {
  fetchSuspensionEvents,
  type SuspensionEvent,
  type SuspensionEventsPage,
} from "../../lib/automated-savings-api";
import { LoadStatePanel } from "../../lib/use-org-scoped-fetch";
import { Tooltip } from "../ui/tooltip";

interface SuspensionEventsTableProps {
  orgId: string;
  accessToken: string | null;
}

type HeaderConfig = {
  label: string;
  tooltip?: string;
  // "right" for tooltips on the table's rightmost columns, so the bubble
  // anchors to the right edge and grows toward the interior instead of
  // overflowing past the table and getting clipped by Tremor's
  // overflow-auto wrapper. Defaults to "left" (see ui/Tooltip).
  align?: "left" | "right";
};

const HEADERS: readonly HeaderConfig[] = [
  { label: "Warehouse Name" },
  { label: "Action" },
  { label: "Reason" },
  {
    label: "Running Clusters",
    tooltip: "Number of clusters running when Greysight polled this event.",
  },
  {
    label: "Resumed At",
    tooltip: "When the warehouse was last resumed before this observation.",
  },
  {
    label: "Observed At",
    tooltip: "When Greysight observed this event.",
    align: "right",
  },
  {
    label: "Uptime",
    tooltip: "How long the warehouse had been running at this observation.",
    align: "right",
  },
];

const HEADER_CELL_CLASS =
  "whitespace-nowrap px-4 py-3.5 text-xs font-semibold text-slate-100";
const BODY_CELL_CLASS = "px-4 py-2 text-xs text-slate-300";

// A column header with an instant, styled explanatory tooltip (see ui/Tooltip),
// mirroring the idiom in warehouse-table.tsx's HeaderWithTooltip. Only renders
// the tooltip trigger when a header has one.
function HeaderCell({ label, tooltip, align }: HeaderConfig) {
  if (!tooltip) {
    return <TableHeaderCell className={HEADER_CELL_CLASS}>{label}</TableHeaderCell>;
  }
  return (
    <TableHeaderCell className={HEADER_CELL_CLASS}>
      <Tooltip
        className="underline decoration-dotted decoration-slate-500 underline-offset-4"
        content={tooltip}
        align={align}
      >
        {label}
      </Tooltip>
    </TableHeaderCell>
  );
}

// Browser-local absolute timestamp; full ISO string available on hover.
function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// Uptime = last-observed minus last-resumed, both already in the event row —
// derived client-side so it costs Supabase nothing. observed_at is the state
// snapshot the suspend decision was made from; created_at is only when the
// event was recorded, so it is deliberately not used here or displayed.
function formatUptime(event: SuspensionEvent): string {
  if (!event.observedResumedOn) return "—";
  const resumed = new Date(event.observedResumedOn).getTime();
  const observed = new Date(event.observedAt).getTime();
  if (Number.isNaN(resumed) || Number.isNaN(observed) || observed < resumed) {
    return "—";
  }
  const seconds = Math.round((observed - resumed) / 1_000);
  return `${seconds.toLocaleString()}s`;
}

const PAGER_BUTTON_CLASS =
  "flex h-8 w-8 items-center justify-center rounded-md border border-hairline text-slate-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40";

function ChevronLeftIcon() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
    >
      <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
    >
      <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SuspensionEventsTable({
  orgId,
  accessToken,
}: SuspensionEventsTableProps) {
  // Keyset pagination: `cursorStack[i]` is the cursor used to fetch page `i`
  // (page 0 is always fetched with cursor `null`). "Previous" is derived
  // purely client-side by replaying the cursor already recorded for that
  // page — the API only exposes a forward `nextCursor`, no "previous" cursor.
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<SuspensionEventsPage | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [navigating, setNavigating] = useState(false);
  const [navError, setNavError] = useState(false);
  // React state updates are async, so two rapid clicks could both observe
  // navigating === false; this ref is the synchronous in-flight guard for
  // prev/next navigation.
  const navInFlightRef = useRef(false);
  const requestSequenceRef = useRef(0);
  // Read at fetch time instead of depending on it directly: Supabase rotates
  // the access token roughly hourly, and depending on `accessToken` here
  // would restart pagination on every rotation.
  const accessTokenRef = useRef(accessToken);
  useEffect(() => {
    accessTokenRef.current = accessToken;
  });

  const loadPage = useCallback(
    (index: number, cursor: string | null, { initial = false } = {}) => {
      const seq = ++requestSequenceRef.current;
      if (initial) {
        setLoadState("loading");
      } else {
        setNavError(false);
      }
      fetchSuspensionEvents(orgId, cursor, {
        accessToken: accessTokenRef.current,
      })
        .then((result) => {
          if (requestSequenceRef.current !== seq) return;
          setPage(result);
          setPageIndex(index);
          setLoadState("ready");
        })
        .catch(() => {
          if (requestSequenceRef.current !== seq) return;
          if (initial) {
            setLoadState("error");
          } else {
            setNavError(true);
          }
        })
        .finally(() => {
          if (requestSequenceRef.current !== seq) return;
          navInFlightRef.current = false;
          setNavigating(false);
        });
    },
    [orgId],
  );

  useEffect(() => {
    // Reset to the loading state before the async fetch resolves so a stale
    // prior org's content is never shown while the new org's data loads.
    // This is derived-state synchronization with the fetch, not a cascading
    // render loop. The shell remounts this component (via `key`) on org
    // switch, so a mount-only effect keyed on `orgId` is sufficient here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCursorStack([null]);
    setPageIndex(0);
    setPage(null);
    setNavError(false);
    setNavigating(false);
    navInFlightRef.current = false;
    loadPage(0, null, { initial: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  const events = page?.events ?? [];

  function handleRetry() {
    setCursorStack([null]);
    setPageIndex(0);
    setPage(null);
    setNavError(false);
    setNavigating(false);
    navInFlightRef.current = false;
    loadPage(0, null, { initial: true });
  }

  function handlePrev() {
    if (navInFlightRef.current || pageIndex === 0) return;
    navInFlightRef.current = true;
    setNavigating(true);
    loadPage(pageIndex - 1, cursorStack[pageIndex - 1]);
  }

  function handleNext() {
    if (navInFlightRef.current || !page?.nextCursor) return;
    navInFlightRef.current = true;
    setNavigating(true);
    const nextCursor = page.nextCursor;
    // Overwrite this page's forward cursor and drop anything deeper than it,
    // instead of only pushing when new — otherwise navigating back and then
    // forward again after new events arrive would replay a stale, deeper
    // cursor on a later Previous.
    setCursorStack((stack) => [...stack.slice(0, pageIndex + 1), nextCursor]);
    loadPage(pageIndex + 1, nextCursor);
  }

  const showPager = pageIndex > 0 || Boolean(page?.nextCursor);

  return (
    <section className="rounded-lg border border-hairline bg-surface p-6">
      <h2 className="text-sm font-semibold text-slate-200">
        Suspension events
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        Suspensions triggered by Greysight in descending order.
      </p>
      <LoadStatePanel
        loadState={loadState}
        loadingMessage="Loading suspension events…"
        errorMessage="We couldn’t load suspension events. Please try again."
        onRetry={handleRetry}
      >
        {events.length === 0 ? (
          <p className="mt-4 text-sm text-slate-400">
            No recorded suspensions yet.
          </p>
        ) : (
          <>
            <Table aria-label="Suspension events" className="mt-4 w-full text-left">
              <TableHead>
                <TableRow>
                  {HEADERS.map((header) => (
                    <HeaderCell key={header.label} {...header} />
                  ))}
                </TableRow>
              </TableHead>
              <TableBody className="divide-y divide-hairline align-top">
                {events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="px-4 py-2 text-xs font-semibold text-slate-100">
                      {event.warehouseName}
                    </TableCell>
                    <TableCell className={`${BODY_CELL_CLASS} capitalize`}>
                      {event.action}
                    </TableCell>
                    <TableCell className={`${BODY_CELL_CLASS} capitalize`}>
                      {event.reason}
                    </TableCell>
                    {/* Null clusters = unknown/unavailable (Standard-tier
                        accounts and malformed worker observations both store
                        null), so it is shown as "—" rather than assumed to be
                        1 cluster. */}
                    <TableCell className={BODY_CELL_CLASS}>
                      {event.observedStartedClusters ?? "—"}
                    </TableCell>
                    <TableCell
                      className={BODY_CELL_CLASS}
                      title={event.observedResumedOn ?? undefined}
                    >
                      {formatTimestamp(event.observedResumedOn)}
                    </TableCell>
                    <TableCell
                      className={BODY_CELL_CLASS}
                      title={event.observedAt}
                    >
                      {formatTimestamp(event.observedAt)}
                    </TableCell>
                    <TableCell className={BODY_CELL_CLASS}>
                      {formatUptime(event)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {showPager ? (
              <div className="mt-4 flex items-center justify-end gap-2">
                {navError ? (
                  <span role="alert" className="mr-auto text-sm text-red-400">
                    Couldn’t load that page. Please try again.
                  </span>
                ) : null}
                <span className="text-xs text-slate-500">
                  Page {pageIndex + 1}
                </span>
                <button
                  type="button"
                  aria-label="Previous page"
                  disabled={pageIndex === 0 || navigating}
                  onClick={handlePrev}
                  className={PAGER_BUTTON_CLASS}
                >
                  <ChevronLeftIcon />
                </button>
                <button
                  type="button"
                  aria-label="Next page"
                  aria-busy={navigating}
                  disabled={!page?.nextCursor || navigating}
                  onClick={handleNext}
                  className={PAGER_BUTTON_CLASS}
                >
                  <ChevronRightIcon />
                </button>
              </div>
            ) : null}
          </>
        )}
      </LoadStatePanel>
    </section>
  );
}
