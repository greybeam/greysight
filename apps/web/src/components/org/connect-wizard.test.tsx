import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConnectWizard from "./connect-wizard";

const originalClipboard = navigator.clipboard;

afterEach(() => {
  cleanup();
  Object.assign(navigator, { clipboard: originalClipboard });
});

function fill() {
  fireEvent.change(screen.getByLabelText(/organization name/i), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText(/account/i), { target: { value: "GOPGUKF-JO19546" } });
  fireEvent.change(screen.getByLabelText(/^user/i), { target: { value: "GREYBEAM_USER" } });
  fireEvent.change(screen.getByLabelText(/role/i), { target: { value: "GREYBEAM_ROLE" } });
  fireEvent.change(screen.getByLabelText(/warehouse/i), { target: { value: "GREYBEAM_WH" } });
  fireEvent.change(screen.getByLabelText(/private key/i), { target: { value: "PEM" } });
}

describe("ConnectWizard", () => {
  it("submits and calls onConnected with the new org id", async () => {
    const connect = vi.fn().mockResolvedValue("org-123");
    const onConnected = vi.fn();
    render(<ConnectWizard connect={connect} onConnected={onConnected} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /test connection & save/i }));
    await waitFor(() => expect(onConnected).toHaveBeenCalledWith("org-123"));
    expect(connect).toHaveBeenCalledWith(
      expect.objectContaining({ orgName: "Acme", account: "GOPGUKF-JO19546" }),
      expect.objectContaining({ accessToken: null }),
    );
  });

  it("shows a loading state while submitting", () => {
    const connect = vi.fn().mockReturnValue(new Promise<string>(() => {}));
    render(<ConnectWizard connect={connect} onConnected={vi.fn()} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /test connection & save/i }));
    const button = screen.getByRole("button", { name: /validating/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("shows the server validation message on failure", async () => {
    const { ConnectValidationError } = await import("../../lib/onboarding-api");
    const connect = vi.fn().mockRejectedValue(new ConnectValidationError("Bad Account Usage access"));
    render(<ConnectWizard connect={connect} onConnected={vi.fn()} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /test connection & save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bad Account Usage access");
  });

  it("copies the setup SQL", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<ConnectWizard connect={vi.fn()} onConnected={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        expect.stringContaining("CREATE USER IF NOT EXISTS"),
      ),
    );
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  it("renders the full setup SQL text", () => {
    const { container } = render(<ConnectWizard connect={vi.fn()} onConnected={vi.fn()} />);
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toContain("CREATE USER IF NOT EXISTS");
    expect(pre?.textContent).toContain("GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER");
  });
});
