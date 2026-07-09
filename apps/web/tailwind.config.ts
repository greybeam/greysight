import type { Config } from "tailwindcss";

import { CHART_COLORS } from "./src/lib/chart-colors";

const tremorChartColors = [
  "slate",
  "gray",
  "zinc",
  "neutral",
  "stone",
  "red",
  "orange",
  "amber",
  "yellow",
  "lime",
  "green",
  "emerald",
  "teal",
  "cyan",
  "sky",
  "blue",
  "indigo",
  "violet",
  "purple",
  "fuchsia",
  "pink",
  "rose",
] as const;

const greybeamChartColorNames = Object.keys(CHART_COLORS);

export const tremorChartColorSafelist = [
  {
    pattern: new RegExp(`^(stroke|fill|text)-(${tremorChartColors.join("|")})-500$`),
  },
  {
    pattern: new RegExp(
      `^(bg|stroke|fill|text|border|ring)-(${greybeamChartColorNames.join("|")})$`,
    ),
    variants: ["dark"],
  },
] satisfies NonNullable<Config["safelist"]>;

const config: Config = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
    "../../node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  safelist: tremorChartColorSafelist,
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef8ff",
          500: "#1477b8",
          700: "#0c4c7c",
        },
        canvas: "#161616",
        ...CHART_COLORS,
        surface: "#1C1C1C",
        hairline: "#2A2A2A",
        tremor: {
          brand: {
            faint: "#eff6ff",
            muted: "#bfdbfe",
            subtle: "#60a5fa",
            DEFAULT: "#3b82f6",
            emphasis: "#1d4ed8",
            inverted: "#ffffff",
          },
          background: {
            muted: "#f9fafb",
            subtle: "#f3f4f6",
            DEFAULT: "#ffffff",
            emphasis: "#374151",
          },
          border: { DEFAULT: "#e5e7eb" },
          ring: { DEFAULT: "#e5e7eb" },
          content: {
            subtle: "#9ca3af",
            DEFAULT: "#6b7280",
            emphasis: "#374151",
            strong: "#111827",
            inverted: "#ffffff",
          },
        },
        "dark-tremor": {
          brand: {
            faint: "#1a1430",
            muted: "#3b2d63",
            subtle: "#7a44c0",
            DEFAULT: "#9F57E7",
            emphasis: "#b985ee",
            inverted: "#161616",
          },
          background: {
            muted: "#161616",
            subtle: "#232323",
            DEFAULT: "#1C1C1C",
            emphasis: "#e5e7eb",
          },
          border: { DEFAULT: "#2A2A2A" },
          ring: { DEFAULT: "#2A2A2A" },
          content: {
            subtle: "#6b7280",
            DEFAULT: "#9ca3af",
            emphasis: "#e5e7eb",
            strong: "#f9fafb",
            inverted: "#000000",
          },
        },
      },
      boxShadow: {
        "tremor-input": "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        "tremor-card":
          "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
        "tremor-dropdown":
          "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
        "dark-tremor-input": "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        "dark-tremor-card":
          "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
        "dark-tremor-dropdown":
          "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
      },
      borderRadius: {
        none: "0",
        sm: "0",
        DEFAULT: "0",
        md: "0",
        lg: "0",
        xl: "0",
        "2xl": "0",
        "3xl": "0",
        full: "0",
        "tremor-small": "0",
        "tremor-default": "0",
        "tremor-full": "0",
      },
      fontFamily: {
        sans: [
          "var(--font-body)",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        display: [
          "var(--font-display)",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
      },
      fontSize: {
        "tremor-label": ["0.75rem", { lineHeight: "1rem" }],
        "tremor-default": ["0.875rem", { lineHeight: "1.25rem" }],
        "tremor-title": ["1.125rem", { lineHeight: "1.75rem" }],
        "tremor-metric": ["1.875rem", { lineHeight: "2.25rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
