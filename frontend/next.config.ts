import type { NextConfig } from "next";

const apiUrl = process.env.KMR_API_URL ?? "http://127.0.0.1:8000";

const dev = process.env.NODE_ENV !== "production";

// Next.js renders small inline scripts and styles, so those two stay allowed; everything else
// (images, fonts, connections, frames, forms) is limited to this origin.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${dev ? " ws: wss:" : ""}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ...(dev
    ? []
    : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // The widget script is meant to be embedded by other sites.
      {
        source: "/widget/:path*",
        headers: [
          { key: "Cache-Control", value: "public, max-age=300" },
          { key: "Cross-Origin-Resource-Policy", value: "cross-origin" },
        ],
      },
    ];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // The browser only ever talks to this origin. /api/* is forwarded to the backend, so the
  // session cookie is first-party and no CORS or cross-site cookie settings are needed.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiUrl}/v1/:path*` },
      // The visitor widget runs on other sites and calls this path; CORS is decided by the API
      // per install key and origin (see backend/app/assistant/cors.py).
      { source: "/widget-api/:path*", destination: `${apiUrl}/public/v1/:path*` },
    ];
  },
};

export default nextConfig;
