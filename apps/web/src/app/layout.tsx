import type { Metadata } from "next";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "Greysight",
  description: "Snowflake cost observability",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
