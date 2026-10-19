"use client";

import { Download, FileText, GitBranch, Mail, Printer } from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { apiRequest } from "@/lib/client-api";
import type { Ticket } from "@/lib/types";

const MAILTO_LIMIT = 1800; // long mailto links break in many mail apps

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * Prepares a redacted ticket for one finding and shows it before anything leaves the page.
 * Copy, download, email and print are explicit clicks; KMR AI itself sends nothing anywhere.
 */
export function TicketPanel({ orgId, issueId }: { orgId: string; issueId: string }) {
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function sendToGithub() {
    if (!ticket) return;
    setSending(true);
    setError(null);
    const result = await apiRequest<{ url: string }>(
      "POST",
      `/organizations/${orgId}/issues/${issueId}/github-issue`,
      { content_sha256: ticket.content_sha256, confirm: true },
    );
    setSending(false);
    if (!result.ok) return setError(result.error.message);
    setSent(result.data.url);
  }

  async function prepare() {
    setBusy(true);
    setError(null);
    const result = await apiRequest<Ticket>(
      "GET",
      `/organizations/${orgId}/issues/${issueId}/ticket`,
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setTicket(result.data);
  }

  function download() {
    if (!ticket) return;
    const url = URL.createObjectURL(new Blob([ticket.content], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = ticket.filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function print() {
    if (!ticket) return;
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;width:0;height:0;border:0";
    frame.srcdoc = `<pre style="font:13px/1.5 system-ui;white-space:pre-wrap">${escapeHtml(ticket.content)}</pre>`;
    frame.onload = () => {
      frame.contentWindow?.print();
      setTimeout(() => frame.remove(), 1000);
    };
    document.body.appendChild(frame);
  }

  const mailto = ticket
    ? `mailto:?subject=${encodeURIComponent(ticket.title)}&body=${encodeURIComponent(ticket.content.slice(0, MAILTO_LIMIT))}`
    : "#";

  return (
    <Card
      title="Share with your developers"
      description="Turn this finding into a ticket. You see the exact text first."
    >
      <div className="flex flex-col gap-4">
        {error ? (
          <Alert tone="danger" title="Could not prepare the ticket">
            {error}
          </Alert>
        ) : null}
        {!ticket ? (
          <div>
            <Button variant="secondary" onClick={prepare} isLoading={busy}>
              <FileText className="size-4" aria-hidden="true" />
              Prepare ticket
            </Button>
          </div>
        ) : (
          <>
            <Alert tone="info" title="Check this before you share it">
              {ticket.notice}
            </Alert>
            <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
              Ticket text (read-only preview)
              <textarea
                readOnly
                value={ticket.content}
                rows={16}
                className="w-full rounded-md border border-line bg-page p-3 font-mono text-xs font-normal text-ink"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <CopyButton value={ticket.content} label="ticket text" />
              <Button variant="secondary" size="sm" onClick={download}>
                <Download className="size-4" aria-hidden="true" />
                Download .md
              </Button>
              <a
                href={mailto}
                className="inline-flex h-10 items-center gap-2 rounded-md border border-line bg-surface px-3 text-sm font-medium text-ink hover:bg-page lg:h-8"
              >
                <Mail className="size-4" aria-hidden="true" />
                Email draft
              </a>
              <Button variant="secondary" size="sm" onClick={print}>
                <Printer className="size-4" aria-hidden="true" />
                Print or save as PDF
              </Button>
            </div>
            {ticket.content.length > MAILTO_LIMIT ? (
              <p className="text-xs text-muted">
                The email draft holds only the first part of the ticket. Use Copy for the full text.
              </p>
            ) : null}
            <section
              aria-label="GitHub issue"
              className="flex flex-col gap-2 border-t border-line pt-3"
            >
              <h3 className="text-sm font-semibold text-ink">Send to GitHub</h3>
              {sent || ticket.github.existing_url ? (
                <p className="text-sm">
                  Sent:{" "}
                  <a
                    href={sent ?? ticket.github.existing_url ?? "#"}
                    className="font-medium break-all text-primary-text underline underline-offset-2"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {sent ?? ticket.github.existing_url}
                  </a>
                </p>
              ) : ticket.github.available ? (
                <>
                  <p className="text-xs text-muted">
                    This creates one issue in <strong>{ticket.github.repository}</strong> with
                    exactly the text above. It cannot be undone from here, and each finding can be
                    sent once.
                  </p>
                  <label className="flex items-start gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      className="mt-1 size-4"
                      checked={confirmed}
                      onChange={(e) => setConfirmed(e.target.checked)}
                    />
                    <span>I have read the text above and want it sent to GitHub.</span>
                  </label>
                  <div>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={sendToGithub}
                      isLoading={sending}
                      disabled={!confirmed}
                    >
                      <GitBranch className="size-4" aria-hidden="true" />
                      Create GitHub issue
                    </Button>
                  </div>
                </>
              ) : (
                <p className="text-xs text-muted">{ticket.github.reason}</p>
              )}
            </section>
          </>
        )}
      </div>
    </Card>
  );
}
