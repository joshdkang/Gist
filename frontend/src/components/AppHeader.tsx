"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Logo } from "@/components/Logo";
import { card } from "@/components/ui";
import { applyPalette, currentPalette, loadAccountPalette, type Palette, savePalette } from "@/lib/palette";
import { createClient } from "@/lib/supabase/client";

/** A small preview of the sentiment colours a palette uses. */
function Swatches() {
  return (
    <span className="flex gap-1" aria-hidden="true">
      {["bg-pos", "bg-mixed", "bg-neutral", "bg-neg"].map((color) => (
        <span key={color} className={`size-3 rounded-full ${color}`} />
      ))}
    </span>
  );
}

/** The account menu under the email: settings and sign out. */
function AccountMenu({ email }: { email: string | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [palette, setPalette] = useState<Palette>("standard");
  const [saveFailed, setSaveFailed] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPalette(currentPalette());
    // The account's choice wins over this browser's, so it follows the user to a new device.
    loadAccountPalette().then((saved) => {
      if (saved) {
        applyPalette(saved);
        setPalette(saved);
      }
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  async function toggleColorBlind() {
    const next: Palette = palette === "colorblind" ? "standard" : "colorblind";
    setPalette(next);
    setSaveFailed(false);
    try {
      await savePalette(next);
    } catch {
      setSaveFailed(true);  // it still applies in this browser
    }
  }

  async function signOut() {
    await createClient().auth.signOut();
    queryClient.clear();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex h-9 items-center gap-2 rounded-lg border border-line-strong bg-surface px-3 text-sm text-fg"
      >
        <span className="hidden max-w-[240px] truncate sm:inline">{email ?? "Account"}</span>
        <span className="sm:hidden">Account</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className={`${card} absolute top-11 right-0 z-20 flex w-[280px] flex-col p-1.5 shadow-lg`}>
          {email && <p className="truncate px-3 pt-2 pb-1 text-sm text-muted sm:hidden">{email}</p>}
          <p className="px-3 pt-2 pb-1 text-xs font-semibold tracking-wide text-muted uppercase">Settings</p>
          <button
            type="button"
            role="switch"
            aria-checked={palette === "colorblind"}
            onClick={toggleColorBlind}
            className="flex items-center justify-between gap-3 rounded-md px-3 py-2.5 text-left hover:bg-subtle"
          >
            <span className="flex flex-col gap-1">
              <span>Colour-blind mode</span>
              <Swatches />
            </span>
            <span
              className={`relative h-6 w-10 shrink-0 rounded-full transition-colors ${
                palette === "colorblind" ? "bg-primary" : "bg-line-strong"
              }`}
              aria-hidden="true"
            >
              <span
                className={`absolute top-0.5 size-5 rounded-full bg-surface shadow transition-[left] ${
                  palette === "colorblind" ? "left-[18px]" : "left-0.5"
                }`}
              />
            </span>
          </button>
          {saveFailed && (
            <p className="px-3 pb-1 text-xs text-muted">Couldn&apos;t save to your account; it applies in this browser.</p>
          )}
          <div className="my-1 border-t border-line" />
          <button type="button" onClick={signOut} className="rounded-md px-3 py-2.5 text-left hover:bg-subtle">
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppHeader() {
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    createClient()
      .auth.getSession()
      .then(({ data }) => setEmail(data.session?.user.email ?? null));
  }, []);

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-[1080px] flex-wrap items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
        <nav className="flex items-center gap-6">
          <Link href="/" aria-label="Gist home" className="py-1.5">
            <Logo />
          </Link>
          <Link href="/" className="px-1 py-2.5 font-medium text-muted hover:text-fg">
            Entities
          </Link>
        </nav>
        <AccountMenu email={email} />
      </div>
    </header>
  );
}
