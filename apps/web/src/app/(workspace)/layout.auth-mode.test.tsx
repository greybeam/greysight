import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../components/workspace/workspace-runtime-shell", () => ({
  WorkspaceRuntimeShell: ({
    authRequired,
    children,
    dataSource,
  }: {
    authRequired: boolean;
    children: React.ReactNode;
    dataSource: string;
  }) => (
    <div>
      <div data-testid="auth-required">{String(authRequired)}</div>
      <div data-testid="data-source">{dataSource}</div>
      {children}
    </div>
  ),
}));

import WorkspaceLayout from "./layout";

describe("WorkspaceLayout auth mode", () => {
  const originalDataSource = process.env.DATA_SOURCE;

  afterEach(() => {
    cleanup();
    if (originalDataSource === undefined) {
      delete process.env.DATA_SOURCE;
    } else {
      process.env.DATA_SOURCE = originalDataSource;
    }
  });

  it("passes Snowflake data source to the runtime shell", () => {
    process.env.DATA_SOURCE = "snowflake";

    render(<WorkspaceLayout>content</WorkspaceLayout>);

    expect(screen.getByTestId("data-source")).toHaveTextContent("snowflake");
  });
});
