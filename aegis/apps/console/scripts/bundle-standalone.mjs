import { cp, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const standalone = resolve(root, ".next/standalone");

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

if (!(await exists(standalone))) {
  console.error("bundle-standalone: .next/standalone is missing; is output set to standalone?");
  process.exit(1);
}

await cp(resolve(root, ".next/static"), resolve(standalone, ".next/static"), { recursive: true });

if (await exists(resolve(root, "public"))) {
  await cp(resolve(root, "public"), resolve(standalone, "public"), { recursive: true });
}

console.log("bundle-standalone: static assets copied into .next/standalone");
