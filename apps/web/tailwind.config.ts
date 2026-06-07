import type { Config } from "tailwindcss";
import forms from "@tailwindcss/forms";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#111827",
          800: "#27313f",
          600: "#52606f",
          400: "#8a96a3",
        },
        slatewash: {
          50: "#f7f9fb",
          100: "#edf2f5",
          200: "#dbe4ea",
        },
        teal: {
          450: "#159a9c",
          550: "#087f83",
          700: "#075e62",
        },
        lime: {
          350: "#b7e45c",
          450: "#98cf38",
        },
        amber: {
          450: "#d99524",
        },
      },
      boxShadow: {
        soft: "0 18px 44px rgba(17, 24, 39, 0.08)",
        line: "0 1px 0 rgba(17, 24, 39, 0.08)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
      borderRadius: {
        product: "6px",
      },
    },
  },
  plugins: [forms],
};

export default config;
