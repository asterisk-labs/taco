import { parquetMetadataAsync, parquetQuery } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import { fail } from "./errors.js";
import { filterColumns } from "./filter.js";

export const PROTECTED_LOCATION_COLUMNS = new Set(["cozip:location", "taco:location"]);

export class TacoParquet {
  /**
   * @param {{ byteLength: number, slice(start: number, end?: number): Promise<ArrayBuffer> }} file
   * @param {string} level
   */
  constructor(file, level) {
    this.file = file;
    this.level = level;
    this.metadataPromise = this.readMetadata();
  }

  async readMetadata() {
    const metadata = await parquetMetadataAsync(this.file);
    const storedLevel = metadata.key_value_metadata?.find((item) => item.key === "taco:level")?.value;
    if (storedLevel !== this.level) {
      fail(
        "INVALID_PARQUET_LEVEL",
        `${this.level} metadata stores taco:level=${JSON.stringify(storedLevel)}`,
      );
    }
    return metadata;
  }

  /**
   * @param {{
   *   columns?: string[],
   *   filter?: Record<string, any>,
   *   rowStart?: number,
   *   rowEnd?: number,
   * }} [options]
   */
  async read(options = {}) {
    validateRows(options.rowStart, options.rowEnd);
    if (
      options.columns !== undefined &&
      (!Array.isArray(options.columns) ||
        options.columns.some((name) => typeof name !== "string" || name.length === 0))
    ) {
      throw new TypeError("taco: columns must be an array of non-empty strings");
    }
    const protectedFilter = filterColumns(options.filter).filter((name) =>
      PROTECTED_LOCATION_COLUMNS.has(name),
    );
    if (protectedFilter.length) {
      fail(
        "PROTECTED_COLUMN",
        `stored reader-owned columns cannot be filtered: ${protectedFilter.join(", ")}`,
      );
    }

    const requested = options.columns ? [...new Set(options.columns)] : undefined;
    let columns = requested?.filter((name) => !PROTECTED_LOCATION_COLUMNS.has(name));
    let sentinel = false;
    if (columns && columns.length === 0) {
      columns = ["internal:current_id"];
      sentinel = true;
    }
    const metadata = await this.metadataPromise;
    const rows = await parquetQuery({
      file: this.file,
      metadata,
      compressors,
      columns,
      filter: options.filter,
      rowStart: options.rowStart,
      rowEnd: options.rowEnd,
      useOffsetIndex: true,
      utf8: false,
    });
    for (const row of rows) {
      for (const name of PROTECTED_LOCATION_COLUMNS) delete row[name];
      if (sentinel) delete row["internal:current_id"];
    }
    return rows;
  }
}

/** @param {number | undefined} start @param {number | undefined} end */
function validateRows(start, end) {
  if (start !== undefined && (!Number.isSafeInteger(start) || start < 0)) {
    throw new RangeError("taco: rowStart must be a non-negative integer");
  }
  if (end !== undefined && (!Number.isSafeInteger(end) || end < 0)) {
    throw new RangeError("taco: rowEnd must be a non-negative integer");
  }
  if (start !== undefined && end !== undefined && start > end) {
    throw new RangeError("taco: rowStart must not exceed rowEnd");
  }
}
