import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#070b10",
          900: "#0b1118",
          800: "#111a23",
          700: "#182430",
          600: "#22323f",
        },
        mist: {
          100: "#e8eef5",
          300: "#b3c0cf",
          500: "#7d8b9c",
        },
        signal: {
          green: "#7ee787",
          blue: "#79c0ff",
          amber: "#f0b429",
          red: "#ff7b72",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
