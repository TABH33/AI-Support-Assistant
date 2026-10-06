/** @type {import('tailwindcss').Config} */
// Tailwind v4 (@tailwindcss/postcss) does not read this file's `theme`
// automatically -- that's a v3 mechanism. The Ctrack-Design-Kit colour/font
// tokens live in src/index.css's `@theme` block instead, which is what
// actually generates the bg-brand-teal/text-surface-page/etc. utilities
// used throughout this app. This file is kept only in case content-glob
// overrides are ever needed; v4's automatic content detection covers this
// project without one.
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  plugins: [],
}
