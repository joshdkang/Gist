import Link from "next/link";

export const buttonPrimary =
  "inline-flex h-11 items-center justify-center rounded-lg bg-primary px-4 font-semibold text-on-primary disabled:cursor-not-allowed disabled:opacity-50";
export const buttonSecondary =
  "inline-flex h-11 items-center justify-center rounded-lg border border-line-strong bg-surface px-4 text-fg disabled:cursor-not-allowed disabled:opacity-50";
export const buttonLink = "rounded px-1 py-2 text-link hover:underline";
export const inputBase = "h-11 rounded-lg border border-line-strong bg-surface px-3 text-fg";
export const inputClass = `${inputBase} w-full`;
export const labelClass = "text-sm font-medium";
export const card = "rounded-xl border border-line bg-surface";

export function PageTitle({ children }: { children: React.ReactNode }) {
  return <h1 className="font-display text-[28px] leading-tight font-semibold">{children}</h1>;
}

export function BackLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="inline-block py-2 text-link hover:underline">
      ← {children}
    </Link>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert" className="rounded-lg border border-warn-line bg-warn-bg px-4 py-3 text-fg">
      {message}
    </p>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="py-8 text-muted" role="status">
      {label}
    </p>
  );
}

export function Page({ children, width = "max-w-[1080px]" }: { children: React.ReactNode; width?: string }) {
  return <main className={`mx-auto w-full ${width} px-4 pt-6 pb-16 sm:px-6 sm:pt-8`}>{children}</main>;
}
