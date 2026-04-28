/** @type {import('next').NextConfig} */
const basePath = process.env.NEXT_PUBLIC_APP_BASE_PATH || process.env.KAMIWAZA_APP_PATH || "";

const nextConfig = {
    output: "standalone",
    basePath: basePath || undefined,
    assetPrefix: basePath || undefined,
};

module.exports = nextConfig;
