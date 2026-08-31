import { defineConfig } from "vitest/config";

// Separate from vite.config.ts on purpose — keeps the dev/build config free
// of test-only concerns. Default "node" environment (not jsdom): the
// initial test suite covers pure store/utility logic with no real DOM
// access, and jsdom's startup cost isn't worth paying until a real
// component-rendering test actually needs it.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
