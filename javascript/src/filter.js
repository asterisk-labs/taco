import { fail } from "./errors.js";

const OPERATORS = new Set(["$gt", "$gte", "$lt", "$lte", "$eq", "$ne", "$in", "$nin", "$not"]);

/**
 * Return every top-level field referenced by a filter.
 *
 * @param {Record<string, any> | undefined} filter
 * @returns {string[]}
 */
export function filterColumns(filter) {
  if (!filter) return [];
  validateFilter(filter);
  if (Array.isArray(filter.$and)) return unique(filter.$and.flatMap(filterColumns));
  if (Array.isArray(filter.$or)) return unique(filter.$or.flatMap(filterColumns));
  if (Array.isArray(filter.$nor)) return unique(filter.$nor.flatMap(filterColumns));
  return unique(Object.keys(filter).map((key) => key.split(".")[0]));
}

/**
 * @param {Record<string, any>} row
 * @param {Record<string, any> | undefined} filter
 * @returns {boolean}
 */
export function matchesFilter(row, filter) {
  if (!filter) return true;
  validateFilter(filter);
  if (Array.isArray(filter.$and)) return filter.$and.every((part) => matchesFilter(row, part));
  if (Array.isArray(filter.$or)) return filter.$or.some((part) => matchesFilter(row, part));
  if (Array.isArray(filter.$nor)) return !filter.$nor.some((part) => matchesFilter(row, part));

  return Object.entries(filter).every(([field, condition]) => {
    const value = resolve(row, field);
    if (!isObject(condition) || Array.isArray(condition)) return equal(value, condition);
    return Object.entries(condition).every(([operator, target]) => {
      if (operator === "$gt") return value > target;
      if (operator === "$gte") return value >= target;
      if (operator === "$lt") return value < target;
      if (operator === "$lte") return value <= target;
      if (operator === "$eq") return equal(value, target);
      if (operator === "$ne") return !equal(value, target);
      if (operator === "$in") return Array.isArray(target) && target.some((item) => equal(value, item));
      if (operator === "$nin") return Array.isArray(target) && !target.some((item) => equal(value, item));
      if (operator === "$not") {
        return !matchesFilter({ [field]: value }, { [field]: target });
      }
      return false;
    });
  });
}

/** @param {Record<string, any>} filter */
function validateFilter(filter) {
  if (!isObject(filter) || Array.isArray(filter)) {
    throw new TypeError("taco: filter must be an object");
  }
  const logical = ["$and", "$or", "$nor"].filter((name) => name in filter);
  if (logical.length) {
    if (logical.length !== 1 || Object.keys(filter).length !== 1 || !Array.isArray(filter[logical[0]])) {
      fail("INVALID_FILTER", "logical filters must contain exactly one of $and, $or, or $nor");
    }
    for (const part of filter[logical[0]]) validateFilter(part);
    return;
  }
  for (const [field, condition] of Object.entries(filter)) {
    if (field.startsWith("$")) fail("INVALID_FILTER", `unknown logical operator ${field}`);
    if (isObject(condition) && !Array.isArray(condition)) {
      for (const operator of Object.keys(condition)) {
        if (!OPERATORS.has(operator)) fail("INVALID_FILTER", `unknown operator ${operator}`);
      }
    }
  }
}

/** @param {Record<string, any>} row @param {string} path */
function resolve(row, path) {
  /** @type {any} */
  let value = row;
  for (const part of path.split(".")) value = value?.[part];
  return value;
}

/** @param {any} left @param {any} right */
function equal(left, right) {
  if (typeof left === "bigint" && typeof right === "number" && Number.isSafeInteger(right)) {
    return left === BigInt(right);
  }
  if (typeof right === "bigint" && typeof left === "number" && Number.isSafeInteger(left)) {
    return BigInt(left) === right;
  }
  return left === right;
}

/** @param {unknown} value */
function isObject(value) {
  return value !== null && typeof value === "object";
}

/** @param {string[]} values */
function unique(values) {
  return [...new Set(values)];
}
