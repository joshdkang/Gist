import { AppHeader } from "@/components/AppHeader";

/** Every signed-in page: the header, then the page. The middleware keeps signed-out visitors out. */
export default function SignedInLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <AppHeader />
      {children}
    </div>
  );
}
