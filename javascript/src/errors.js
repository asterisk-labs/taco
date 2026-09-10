/** Error raised when a TACO source or operation is invalid. */
export class TacoError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   * @param {ErrorOptions} [options]
   */
  constructor(code, message, options) {
    super(`taco ${code}: ${message}`, options);
    this.name = "TacoError";
    this.code = code;
  }
}

/**
 * @param {string} code
 * @param {string} message
 * @returns {never}
 */
export function fail(code, message) {
  throw new TacoError(code, message);
}
