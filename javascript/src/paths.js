import { fail } from "./errors.js";

const FORBIDDEN_COMPONENT = /[<>:"\\|?]/;

/**
 * Validate and return a normalized relative POSIX path.
 *
 * @param {unknown} value
 * @param {string} context
 * @param {{ allowGlob?: boolean }} [options]
 * @returns {string}
 */
export function relativePath(value, context, options = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("INVALID_PATH", `${context} must be a non-empty string`);
  }
  if (!/^[\x20-\x7e]+$/.test(value)) {
    fail("INVALID_PATH", `${context} must use printable ASCII`);
  }
  if (value.startsWith("/") || value.endsWith("/") || value.includes("\\")) {
    fail("INVALID_PATH", `${context} must be a relative POSIX path`);
  }
  const parts = value.split("/");
  for (const part of parts) {
    if (
      part.length === 0 ||
      part === "." ||
      part === ".." ||
      part.endsWith(" ") ||
      part.endsWith(".") ||
      part.includes("__") ||
      FORBIDDEN_COMPONENT.test(part)
    ) {
      fail("INVALID_PATH", `${context} contains an invalid component ${JSON.stringify(part)}`);
    }
    if (!options.allowGlob && /[*\[\]]/.test(part)) {
      fail("INVALID_PATH", `${context} contains reserved sequence characters`);
    }
  }
  return value;
}

/** @param {string} path */
export function encodeRelativePath(path) {
  return path.split("/").map((part) => encodeURIComponent(part)).join("/");
}

/** @param {string} level */
export function levelFilename(level) {
  return `${level.replaceAll("/", "__")}.parquet`;
}

/** @param {string} level */
export function parentLevel(level) {
  if (level === "children") return "sample";
  const folder = level.slice("children/".length);
  const slash = folder.lastIndexOf("/");
  return slash < 0 ? "children" : `children/${folder.slice(0, slash)}`;
}

/** @param {string} path */
export function contractPath(path) {
  const slash = path.indexOf("/");
  return slash < 0 ? "" : path.slice(slash + 1);
}

/** @param {string} path */
export function basename(path) {
  const slash = path.lastIndexOf("/");
  return slash < 0 ? path : path.slice(slash + 1);
}
