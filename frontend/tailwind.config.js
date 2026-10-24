/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Vazirmatn", "IRANSans", "Tahoma", "system-ui", "sans-serif"],
        mono: ["DejaVu Sans Mono", "Consolas", "monospace"],
      },
      colors: {
        brand: {
          50: "#eef4fb",
          100: "#d8e6f6",
          200: "#b3cdec",
          300: "#82abdd",
          400: "#4f83c8",
          500: "#2f63ad",
          600: "#1f4e79",
          700: "#1a3f61",
          800: "#16324c",
          900: "#12283c",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.1)",
      },
    },
  },
  plugins: [],
};
