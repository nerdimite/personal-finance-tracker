import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(process.cwd()),
  serverExternalPackages: ["pg"],
  poweredByHeader: false,
  agentRules: false,
};

export default nextConfig;
