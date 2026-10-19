// Run with the frontend's vitest: `make ext-test` (no separate install needed).
export default {
  test: {
    environment: "jsdom",
    globals: true,
    include: ["test/**/*.test.js"],
    css: false,
  },
};
