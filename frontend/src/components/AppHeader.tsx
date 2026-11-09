"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/Logo";
import { createClient } from "@/lib/supabase/client";

export function AppHeader() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    createClient()
      .auth.getSession()
      .then(({ data }) => setEmail(data.session?.user.email ?? null));
  }, []);

  async function signOut() {
    await createClient().auth.signOut();
    queryClient.clear();
    router.replace("/login");
    router.refresh();
  }

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
        <div className="flex items-center gap-3 text-sm text-muted">
          {email && <span className="hidden sm:inline">{email}</span>}
          <button
            type="button"
            onClick={signOut}
            className="h-9 rounded-lg border border-line-strong bg-surface px-3 text-fg"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
