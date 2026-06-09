import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
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
