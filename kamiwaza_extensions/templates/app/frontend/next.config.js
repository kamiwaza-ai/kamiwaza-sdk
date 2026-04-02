/** @type {import('next').NextConfig} */
const basePath = process.env.KAMIWAZA_APP_PATH || "";

const nextConfig = {
    output: "standalone",
    basePath: basePath || undefined,
    assetPrefix: basePath || undefined,
};

module.exports = nextConfig;
