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
  const originalAuthRequired = process.env.AUTH_REQUIRED;
  const originalPublicAuthRequired = process.env.NEXT_PUBLIC_AUTH_REQUIRED;
  const originalDataSource = process.env.DATA_SOURCE;

  afterEach(() => {
    cleanup();
    if (originalAuthRequired === undefined) {
      delete process.env.AUTH_REQUIRED;
    } else {
      process.env.AUTH_REQUIRED = originalAuthRequired;
    }

    if (originalPublicAuthRequired === undefined) {
      delete process.env.NEXT_PUBLIC_AUTH_REQUIRED;
    } else {
      process.env.NEXT_PUBLIC_AUTH_REQUIRED = originalPublicAuthRequired;
    }
    if (originalDataSource === undefined) {
      delete process.env.DATA_SOURCE;
    } else {
      process.env.DATA_SOURCE = originalDataSource;
    }
  });

  it("honors NEXT_PUBLIC_AUTH_REQUIRED when AUTH_REQUIRED is absent", () => {
    delete process.env.AUTH_REQUIRED;
    process.env.NEXT_PUBLIC_AUTH_REQUIRED = "true";

    render(<WorkspaceLayout>content</WorkspaceLayout>);

    expect(screen.getByTestId("auth-required")).toHaveTextContent("true");
  });

  it("passes Snowflake data source to the runtime shell", () => {
    process.env.DATA_SOURCE = "snowflake";

    render(<WorkspaceLayout>content</WorkspaceLayout>);

    expect(screen.getByTestId("data-source")).toHaveTextContent("snowflake");
  });
});
