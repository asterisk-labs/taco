import { fail } from "../errors.js";

const OPERATORS = new Set(["$gt", "$gte", "$lt", "$lte", "$eq", "$ne", "$in", "$nin", "$not"]);

/**
 * Return every field path referenced by a filter.
 *
 * @param {Record<string, any> | undefined} filter
 * @returns {string[]}
 */
export function filterPaths(filter) {
  if (!filter) return [];
  validateFilter(filter);
  if (Array.isArray(filter.$and)) return unique(filter.$and.flatMap(filterPaths));
  if (Array.isArray(filter.$or)) return unique(filter.$or.flatMap(filterPaths));
  if (Array.isArray(filter.$nor)) return unique(filter.$nor.flatMap(filterPaths));
  return unique(Object.keys(filter));
}

/**
 * Return every top-level field referenced by a filter.
 *
 * @param {Record<string, any> | undefined} filter
 * @returns {string[]}
 */
export function filterColumns(filter) {
  return unique(filterPaths(filter).map((path) => path.split(".")[0]));
}

/**
 * Wrap Date and byte-array conditions in $eq for hyparquet.
 *
 * @param {Record<string, any>} filter
 * @returns {Record<string, any>}
 */
export function parquetFilter(filter) {
  if (Array.isArray(filter.$and)) return { $and: filter.$and.map(parquetFilter) };
  if (Array.isArray(filter.$or)) return { $or: filter.$or.map(parquetFilter) };
  if (Array.isArray(filter.$nor)) return { $nor: filter.$nor.map(parquetFilter) };
  return Object.fromEntries(
    Object.entries(filter).map(([field, condition]) => [field, parquetCondition(condition)]),
  );
}

/** @param {any} condition @returns {any} */
function parquetCondition(condition) {
  if (isPlainObject(condition)) {
    return "$not" in condition ? { ...condition, $not: parquetCondition(condition.$not) } : condition;
  }
  return isObject(condition) && !Array.isArray(condition) ? { $eq: condition } : condition;
}

/**
 * Apply hyparquet's filter rules to a fully assembled row.
 *
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
  return Object.entries(filter).every(([field, condition]) => test(resolve(row, field), condition));
}

/** @param {any} value @param {any} condition @returns {boolean} */
function test(value, condition) {
  if (!isPlainObject(condition)) return equal(value, condition);
  const present = value !== null && value !== undefined;
  return Object.entries(condition).every(([operator, target]) => {
    if (operator === "$gt") return present && value > target;
    if (operator === "$gte") return present && value >= target;
    if (operator === "$lt") return present && value < target;
    if (operator === "$lte") return present && value <= target;
    if (operator === "$eq") return equal(value, target);
    if (operator === "$ne") return !equal(value, target);
    if (operator === "$in") return Array.isArray(target) && isIn(value, target);
    if (operator === "$nin") return Array.isArray(target) && !isIn(value, target);
    if (operator === "$not") return !test(value, target);
    return false;
  });
}

/**
 * Test a value or any of its array items against the targets.
 *
 * @param {any} value
 * @param {any[]} targets
 */
function isIn(value, targets) {
  return targets.some((target) => (
    equal(value, target) || (Array.isArray(value) && value.some((item) => equal(item, target)))
  ));
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
    validateCondition(condition);
  }
}

/** @param {any} condition */
function validateCondition(condition) {
  if (!isPlainObject(condition)) return;
  for (const [operator, target] of Object.entries(condition)) {
    if (!OPERATORS.has(operator)) fail("INVALID_FILTER", `unknown operator ${operator}`);
    if (operator === "$not") validateCondition(target);
  }
}

/** @param {Record<string, any>} row @param {string} path */
function resolve(row, path) {
  // A wide column may itself contain a dot.
  if (Object.hasOwn(row, path)) return row[path];
  /** @type {any} */
  let value = row;
  for (const part of path.split(".")) value = value?.[part];
  return value;
}

/** @param {any} left @param {any} right @returns {boolean} */
function equal(left, right) {
  // Match missing values with null, as hyparquet does.
  if (left == null || right == null) return left == null && right == null;
  if (typeof left === "bigint" && typeof right === "number" && Number.isSafeInteger(right)) {
    return left === BigInt(right);
  }
  if (typeof right === "bigint" && typeof left === "number" && Number.isSafeInteger(left)) {
    return BigInt(left) === right;
  }
  if (left instanceof Date && right instanceof Date) return left.getTime() === right.getTime();
  if (Array.isArray(left) && Array.isArray(right)) return equalItems(left, right);
  if (left instanceof Uint8Array && right instanceof Uint8Array) return equalItems(left, right);
  if (isPlainObject(left) && isPlainObject(right)) {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length &&
      keys.every((key) => Object.hasOwn(right, key) && equal(left[key], right[key]));
  }
  return left === right;
}

/** @param {ArrayLike<any>} left @param {ArrayLike<any>} right */
function equalItems(left, right) {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index++) {
    if (!equal(left[index], right[index])) return false;
  }
  return true;
}

/**
 * Only plain objects hold operators; Dates and arrays are values.
 *
 * @param {unknown} value
 */
function isPlainObject(value) {
  if (!isObject(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/** @param {unknown} value */
function isObject(value) {
  return value !== null && typeof value === "object";
}

/** @param {string[]} values */
function unique(values) {
  return [...new Set(values)];
}
