import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        kw: {
          primary: "#00c07f",
          secondary: "#1e88e5",
          bg: "#121212",
          surface: "#1e1e1e",
          elevated: "#2d2d2d",
          "text-primary": "#ffffff",
          "text-secondary": "#b0b0b0",
          error: "#f44336",
          warning: "#ff9800",
          success: "#00c07f",
          info: "#29b6f6",
        },
      },
      fontFamily: {
        heading: ["var(--font-montserrat)", "sans-serif"],
        mono: ["var(--font-fira-code)", "monospace"],
      },
      animation: {
        "glow-pulse": "glow-pulse 2s ease-in-out infinite alternate",
      },
      keyframes: {
        "glow-pulse": {
          "0%": { boxShadow: "0 0 5px rgba(0, 192, 127, 0.3)" },
          "100%": { boxShadow: "0 0 20px rgba(0, 192, 127, 0.6)" },
        },
      },
      boxShadow: {
        "glow-teal": "0 0 15px rgba(0, 192, 127, 0.3)",
        "glow-blue": "0 0 15px rgba(30, 136, 229, 0.3)",
        "glow-error": "0 0 15px rgba(244, 67, 54, 0.3)",
      },
    },
  },
  plugins: [],
};

export default config;
