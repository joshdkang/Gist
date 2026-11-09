"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo } from "@/components/Logo";
import { buttonLink, buttonPrimary, ErrorNote, inputClass, labelClass } from "@/components/ui";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const signingUp = mode === "signup";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    const auth = createClient().auth;
    if (signingUp) {
      const { data, error } = await auth.signUp({
        email,
        password,
        options: { emailRedirectTo: `${window.location.origin}/` },
      });
      setBusy(false);
      if (error) return setError(error.message);
      if (!data.session) {
        // Email confirmation is on: the account exists but can't sign in until the link is clicked.
        setMode("signin");
        return setNotice(`We sent a confirmation link to ${email}. Click it, then sign in.`);
      }
    } else {
      const { error } = await auth.signInWithPassword({ email, password });
      setBusy(false);
      if (error) return setError(error.message);
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-[380px] flex-col gap-5 rounded-xl border border-line bg-surface p-8"
      >
        <Logo size={28} />
        <div>
          <h1 className="mb-1 text-xl font-semibold">{signingUp ? "Create an account" : "Sign in"}</h1>
          <p className="text-muted">
            {signingUp
              ? "We will email you a link to confirm your address before you can sign in."
              : "See what your reviews keep saying, and how it changes."}
          </p>
        </div>
        {notice && (
          <p role="status" className="rounded-lg border border-info-line bg-info-bg px-4 py-3">
            {notice}
          </p>
        )}
        <ErrorNote error={error} />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="email" className={labelClass}>
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="password" className={labelClass}>
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={6}
            autoComplete={signingUp ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
        </div>
        <button type="submit" disabled={busy} className={buttonPrimary}>
          {busy ? "Please wait…" : signingUp ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          onClick={() => {
            setMode(signingUp ? "signin" : "signup");
            setError(null);
          }}
          className={`${buttonLink} text-sm`}
        >
          {signingUp ? "Have an account? Sign in" : "No account? Create one"}
        </button>
      </form>
    </main>
  );
}
