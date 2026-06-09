"use client";

import { Badge } from "@tremor/react";

type ApiHealthProps = {
  status?: "ok" | "unknown" | "error";
};

export default function ApiHealth({ status = "unknown" }: ApiHealthProps) {
  if (status === "ok") {
    return (
      <div className="flex items-center gap-3" data-testid="api-health">
        <Badge color="emerald">Connected</Badge>
        <span className="text-sm font-medium text-slate-900">API healthy</span>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex items-center gap-3" data-testid="api-health">
        <Badge color="rose">Unavailable</Badge>
        <span className="text-sm font-medium text-slate-900">API unavailable</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3" data-testid="api-health">
      <Badge color="slate">Pending</Badge>
      <span className="text-sm font-medium text-slate-900">API status pending</span>
    </div>
  );
}
