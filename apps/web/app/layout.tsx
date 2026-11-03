import type { Metadata, Viewport } from "next";
import { Anton, Poppins } from "next/font/google";
import { Analytics } from "@/components/analytics";
import { SiteFooter, SiteHeader } from "@/components/site-chrome";
import { SITE_URL } from "@/lib/site";
import "./globals.css";

const anton = Anton({ subsets: ["latin"], weight: "400", variable: "--font-anton", display: "swap" });
const poppins = Poppins({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-poppins", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Anime News: a headline in, an anime news episode out", template: "%s | Anime News" },
  description:
    "Open-source anime news studio. Type a headline, get a storyboard, render stills and Seedance shots through Comfy Router, and cut a one-minute anime news episode on your own machine.",
  icons: { icon: "/brand/an-mark-small.png" },
  openGraph: {
    title: "Anime News",
    description: "A headline in, an anime news episode out. Open source, runs on your machine.",
    images: ["/episodes/spam-factory.jpg"],
  },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = { themeColor: "#000000", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${anton.variable} ${poppins.variable}`}>
      <body className="min-h-screen">
        <Analytics />
        <SiteHeader />
        <main>{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
