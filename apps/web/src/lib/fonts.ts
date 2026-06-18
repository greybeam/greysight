import { TASA_Orbiter } from "next/font/google";
import localFont from "next/font/local";

// Display typeface for the product wordmark and section headers. Loaded as a
// variable font (weight axis 400-800) and exposed as the `--font-display` CSS
// variable so Tailwind's `font-display` utility can target it.
export const fontDisplay = TASA_Orbiter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});

// Body typeface (Satoshi). Self-hosted via next/font/local from a single
// variable file covering weights 300-900, exposed as `--font-body` so it backs
// Tailwind's default `font-sans` stack and the global body font.
export const fontBody = localFont({
  src: "../../public/fonts/Satoshi-Variable.woff2",
  display: "swap",
  variable: "--font-body",
  weight: "300 900",
});
