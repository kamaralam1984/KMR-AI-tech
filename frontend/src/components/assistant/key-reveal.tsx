"use client";

import { Alert } from "@/components/ui/alert";
import { CopyButton } from "@/components/ui/copy-button";
import type { AssistantInstall } from "@/lib/types";

/** Shows a new install key once, with the snippet already filled in. */
export function KeyReveal({ install }: { install: AssistantInstall & { install_key: string } }) {
  const snippet = install.snippet.replace("PASTE_YOUR_INSTALL_KEY", install.install_key);
  return (
    <div className="mt-5" role="status">
      <Alert tone="warning" title="Copy this now. It will not be shown again.">
        <p className="break-all font-mono text-xs">{install.install_key}</p>
        <pre className="mt-3 max-w-full overflow-x-auto rounded-md border border-line bg-page p-3 text-xs whitespace-pre-wrap break-all">
          {snippet}
        </pre>
        <div className="mt-3 flex flex-wrap gap-2">
          <CopyButton value={install.install_key} label="install key" />
          <CopyButton value={snippet} label="install snippet" />
        </div>
      </Alert>
    </div>
  );
}
