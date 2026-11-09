export function Logo({ size = 24 }: { size?: number }) {
  return (
    <span className="flex items-center gap-2">
      <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
        <rect width="28" height="28" rx="7" fill="#0f172a" />
        <rect x="7" y="16" width="3" height="5" rx="1" fill="#93c5fd" />
        <rect x="12.5" y="11" width="3" height="10" rx="1" fill="#ffffff" />
        <rect x="18" y="7" width="3" height="14" rx="1" fill="#fdba74" />
      </svg>
      <span className="font-display text-[19px] font-semibold">Gist</span>
    </span>
  );
}
