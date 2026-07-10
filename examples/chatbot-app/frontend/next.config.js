// App Garden runtime-path contract: the wrapper owns output/basePath/
// assetPrefix per build variant (KZ_NEXT_BUILD_VARIANT=path|port at image
// build; unset under `next dev`). Do NOT set basePath, assetPrefix, or
// NEXT_PUBLIC_APP_BASE_PATH here — the deployment prefix is applied at
// container start by relocation, not at build.
const { withKamiwazaAppGarden } = require("@kamiwaza-ai/extensions-lib/next-config");

module.exports = withKamiwazaAppGarden({
    output: "standalone",
});
