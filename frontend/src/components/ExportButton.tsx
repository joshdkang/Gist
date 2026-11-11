"use client";

import { useState } from "react";

import { downloadFile } from "@/lib/api";

/** A button that downloads one of the API's CSV exports, with the page's filters in `path`. */
export function ExportButton({ path, label = "Export CSV" }: { path: string; label?: string }) {
  const [state, setState] = useState<"idle" | "working" | "failed">("idle");
  const [error, setError] = useState("");

  async function download() {
    setState("working");
    try {
      await downloadFile(path);
      setState("idle");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      setState("failed");
    }
  }

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={download}
        disabled={state === "working"}
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-sm text-fg hover:bg-subtle disabled:cursor-wait disabled:opacity-60"
      >
        <span aria-hidden="true">↓</span>
        {state === "working" ? "Preparing…" : label}
      </button>
      {state === "failed" && (
        <span role="alert" className="text-xs text-neg-fg">
          Export failed: {error}
        </span>
      )}
    </span>
  );
}
