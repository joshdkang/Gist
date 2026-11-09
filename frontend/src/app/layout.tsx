import type { Metadata } from "next";
import { Inter, Space_Grotesk } from "next/font/google";

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
    <html lang="en">
      <body className={`${inter.variable} ${spaceGrotesk.variable} font-sans text-[15px] leading-normal antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
