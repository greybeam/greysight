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
  ],
  safelist: tremorChartColorSafelist,
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef8ff",
          500: "#1477b8",
          700: "#0c4c7c",
        },
        ...CHART_COLORS,
      },
    },
  },
  plugins: [],
};

export default config;
