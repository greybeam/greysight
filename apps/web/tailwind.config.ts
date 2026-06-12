import type { Config } from "tailwindcss";

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

export const tremorChartColorSafelist = [
  {
    pattern: new RegExp(`^(stroke|fill|text)-(${tremorChartColors.join("|")})-500$`),
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
      },
    },
  },
  plugins: [],
};

export default config;
