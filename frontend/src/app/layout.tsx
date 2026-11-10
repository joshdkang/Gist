import type { Metadata } from "next";
import { Inter, Space_Grotesk } from "next/font/google";

import { PALETTE_BOOT_SCRIPT } from "@/lib/palette";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-space-grotesk" });

export const metadata: Metadata = {
  title: "Gist",
  description: "Recurring themes, sentiment and trends from your reviews.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The boot script may set data-palette before React hydrates, so the attribute can differ.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PALETTE_BOOT_SCRIPT }} />
      </head>
      <body className={`${inter.variable} ${spaceGrotesk.variable} font-sans text-[15px] leading-normal antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
