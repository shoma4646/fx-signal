import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'export',
  basePath: '/stella-trader',
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
