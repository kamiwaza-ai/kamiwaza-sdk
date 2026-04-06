import { cp, mkdir, readdir, rm, symlink } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";

const APP_DIR = "/app";
const RUNTIME_DIR = "/tmp/app-runtime";
const NEXT_CLI = path.join(APP_DIR, "node_modules/next/dist/bin/next");
const SIGNAL_EXIT_CODES = {
  SIGINT: 130,
  SIGTERM: 143,
};

let activeChild = null;

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (activeChild && activeChild.exitCode === null && activeChild.signalCode === null) {
      activeChild.kill(signal);
      return;
    }
    process.exit(SIGNAL_EXIT_CODES[signal] ?? 1);
  });
}

function isExpandedValue(s) {
  return s && !s.includes("${");
}

function resolveBasePath() {
  const routingMode = process.env.KAMIWAZA_ROUTING_MODE;
  const configuredBasePath =
    (isExpandedValue(process.env.NEXT_PUBLIC_APP_BASE_PATH) && process.env.NEXT_PUBLIC_APP_BASE_PATH) ||
    process.env.KAMIWAZA_APP_PATH || "";

  if (routingMode === "path" || (!routingMode && configuredBasePath)) {
    return configuredBasePath.replace(/\/+$/, "") || "/";
  }

  return "";
}

async function prepareRuntimeDir() {
  await rm(RUNTIME_DIR, { recursive: true, force: true });
  await mkdir(RUNTIME_DIR, { recursive: true });
  const entries = await readdir(APP_DIR);
  for (const entry of entries) {
    if (["node_modules", "start.mjs"].includes(entry)) {
      continue;
    }
    await cp(path.join(APP_DIR, entry), path.join(RUNTIME_DIR, entry), { recursive: true });
  }
  await symlink(path.join(APP_DIR, "node_modules"), path.join(RUNTIME_DIR, "node_modules"));
}

function runNext(command, cwd, env) {
  const args = [NEXT_CLI, command];
  if (command === "start") {
    args.push("--hostname", "0.0.0.0", "--port", process.env.PORT || "3000");
  }

  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, {
      cwd,
      env,
      stdio: "inherit",
    });
    activeChild = child;

    child.once("exit", (code, signal) => {
      if (activeChild === child) {
        activeChild = null;
      }
      if (signal) {
        resolve(SIGNAL_EXIT_CODES[signal] ?? 1);
        return;
      }
      resolve(code ?? 1);
    });
    child.once("error", (error) => {
      if (activeChild === child) {
        activeChild = null;
      }
      reject(error);
    });
  });
}

async function main() {
  const basePath = resolveBasePath();
  const env = {
    ...process.env,
    NEXT_TELEMETRY_DISABLED: "1",
    TMPDIR: process.env.TMPDIR || "/tmp",
  };

  if (basePath) {
    env.NEXT_PUBLIC_APP_BASE_PATH = basePath;
  } else {
    delete env.NEXT_PUBLIC_APP_BASE_PATH;
  }

  await prepareRuntimeDir();

  const buildExitCode = await runNext("build", RUNTIME_DIR, env);
  if (buildExitCode !== 0) {
    process.exit(buildExitCode);
  }

  const startExitCode = await runNext("start", RUNTIME_DIR, env);
  process.exit(startExitCode);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
