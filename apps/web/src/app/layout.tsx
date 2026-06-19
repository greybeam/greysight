import type { Metadata } from "next";

import { fontBody, fontDisplay } from "../lib/fonts";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "Greybeam | Greysight",
  description: "Snowflake cost observability",
  icons: {
    icon: "/greybeam_assets/greybeam_logo.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${fontDisplay.variable} ${fontBody.variable}`}>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
