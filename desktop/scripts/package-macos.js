"use strict";

const { execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { notarize, staple } = require("@electron/notarize");
const { packager } = require("@electron/packager");

const root = path.resolve(__dirname, "..");
const out = path.join(root, "dist");

function required(name) {
  const value = String(process.env[name] || "").trim();
  if (!value) throw new Error(`Missing required release credential: ${name}`);
  return value;
}

async function main() {
  const identity = required("CSC_NAME");
  const appleId = required("APPLE_ID");
  const appleIdPassword = required("APPLE_APP_SPECIFIC_PASSWORD");
  const teamId = required("APPLE_TEAM_ID");
  const arch = process.env.ANDROID_AGENT_DESKTOP_ARCH || process.arch;
  const [appPath] = await packager({
    dir: root,
    out,
    overwrite: true,
    platform: "darwin",
    arch,
    name: "Android Agent",
    appBundleId: "com.androidagent.desktop",
    appCategoryType: "public.app-category.developer-tools",
    asar: true,
    prune: true,
    osxSign: {
      identity,
      hardenedRuntime: true,
      "gatekeeper-assess": false,
    },
    ignore: [
      /^\/dist(?:\/|$)/,
      /^\/tests(?:\/|$)/,
    ],
  });

  await notarize({
    appPath,
    appleId,
    appleIdPassword,
    teamId,
  });
  await staple({ appPath });
  execFileSync(
    "codesign",
    ["--verify", "--deep", "--strict", "--verbose=2", appPath],
    { stdio: "inherit" },
  );
  execFileSync(
    "spctl",
    ["--assess", "--type", "execute", "--verbose=2", appPath],
    { stdio: "inherit" },
  );
  const zipName = `Android-Agent-${arch}.zip`;
  const zipPath = path.join(out, zipName);
  execFileSync(
    "ditto",
    ["-c", "-k", "--sequesterRsrc", "--keepParent", appPath, zipPath],
    { stdio: "inherit" },
  );
  const zip = fs.readFileSync(zipPath);
  const sha512 = crypto.createHash("sha512").update(zip).digest("base64");
  const version = require(path.join(root, "package.json")).version;
  const metadata = [
    `version: ${version}`,
    "files:",
    `  - url: ${zipName}`,
    `    sha512: ${sha512}`,
    `    size: ${zip.length}`,
    `path: ${zipName}`,
    `sha512: ${sha512}`,
    `releaseDate: ${new Date().toISOString()}`,
    "",
  ].join("\n");
  fs.writeFileSync(path.join(out, "latest-mac.yml"), metadata, "utf8");
  console.log(`Signed, notarized release: ${zipPath}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
