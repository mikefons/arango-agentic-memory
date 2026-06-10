import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The adapter is a workspace package consumed via a file: dependency.
  transpilePackages: ["@arango-memory/vercel"],
};

export default nextConfig;
