import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunStatus } from "./run-status";

describe("RunStatus", () => {
  it("renders loading state", () => {
    render(<RunStatus status="loading" />);

    expect(screen.getByText("Loading dashboard data")).toBeInTheDocument();
  });

  it("renders running state", () => {
    render(<RunStatus status="running" />);

    expect(screen.getByText("Analysis running")).toBeInTheDocument();
  });

  it("renders completed state", () => {
    render(<RunStatus status="completed" />);

    expect(screen.getByText("Analysis complete")).toBeInTheDocument();
  });

  it("renders failed run errors as user-safe messages", () => {
    render(
      <RunStatus
        status="failed"
        message="Could not access Snowflake Account Usage."
      />,
    );

    expect(
      screen.getByText("Could not access Snowflake Account Usage."),
    ).toBeInTheDocument();
  });

  it("renders expired dataset state", () => {
    render(
      <RunStatus
        status="expired"
        message="Run data expired. Start a new analysis."
      />,
    );

    expect(
      screen.getByText("Run data expired. Start a new analysis."),
    ).toBeInTheDocument();
  });
});
