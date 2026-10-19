import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login-form";

const replace = vi.fn();
const refresh = vi.fn();
let nextParam: string | null = null;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
  useSearchParams: () => ({ get: () => nextParam }),
}));

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

beforeEach(() => {
  replace.mockReset();
  refresh.mockReset();
  apiRequest.mockReset();
  nextParam = null;
});

async function fill(email: string, password: string) {
  await userEvent.type(screen.getByLabelText(/email/i), email);
  await userEvent.type(screen.getByLabelText(/^password/i), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  it("validates before calling the API", async () => {
    render(<LoginForm />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter your email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("shows the server's message and stays on the page when sign-in fails", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: { status: 401, message: "Invalid email or password.", fieldErrors: {} },
    });
    render(<LoginForm />);
    await fill("a@example.com", "wrong password here");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(replace).not.toHaveBeenCalled();
  });

  it("signs in without a CSRF token and goes to the dashboard", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { id: "u1" } });
    render(<LoginForm />);
    await fill("a@example.com", "correct horse battery");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(apiRequest).toHaveBeenCalledWith(
      "POST",
      "/auth/login",
      { email: "a@example.com", password: "correct horse battery" },
      { csrf: false },
    );
  });

  it("follows a safe ?next= and ignores an off-site one", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { id: "u1" } });
    nextParam = "/projects";
    const { unmount } = render(<LoginForm />);
    await fill("a@example.com", "correct horse battery");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/projects"));
    unmount();

    replace.mockReset();
    nextParam = "https://evil.example";
    render(<LoginForm />);
    await fill("a@example.com", "correct horse battery");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("can reveal the password", async () => {
    render(<LoginForm />);
    const field = screen.getByLabelText(/^password/i);
    expect(field).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByLabelText("Show password"));
    expect(field).toHaveAttribute("type", "text");
  });
});
