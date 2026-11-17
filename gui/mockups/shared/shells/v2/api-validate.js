/* Mock server core: check a request against the operation's Params, as the
 * real server does (src/wgsextract_cli/server/operations.py,
 * params_from_json): a JSON object, every required parameter, no unknown
 * ones, each of the right type, enums from their values, absolute paths and
 * no URLs. The schema is shared/data/api-schema.js, generated from the
 * server's OpenAPI document. "_mock" is the mockups' own hint and passes.
 * Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;

  function fail(message) {
    M.fail(400, "usage", message);
  }

  function isAbsolute(text) {
    return /^(\/|[A-Za-z]:[\\/]|\\\\)/.test(text);
  }

  function checkOne(name, f, value) {
    if (value === null) {
      if (!f.nullable) fail(JSON.stringify(name) + " must be set (it can't be null).");
      return;
    }
    if (f.type === "array") {
      var numbers = f.items === "number" || f.items === "integer";
      var ok = Array.isArray(value) && value.every(function (v) {
        return numbers ? typeof v === "number" : typeof v === "string";
      });
      if (!ok) fail(JSON.stringify(name) + " must be " + (numbers ? "an array of numbers." : "an array of strings."));
      if (f.path) value.forEach(function (v) {
        checkPath(name, v);
      });
      return;
    }
    if (f.type === "boolean" && typeof value !== "boolean") fail(JSON.stringify(name) + " must be true or false.");
    if (f.type === "integer" && (typeof value !== "number" || Math.floor(value) !== value)) fail(JSON.stringify(name) + " must be an integer.");
    if (f.type === "number" && typeof value !== "number") fail(JSON.stringify(name) + " must be a number.");
    if (f.type === "string" && typeof value !== "string") fail(JSON.stringify(name) + " must be a string.");
    if (f.enum && f.enum.indexOf(value) < 0) fail(JSON.stringify(name) + " must be one of: " + f.enum.join(", ") + ".");
    if (f.path) checkPath(name, value);
  }

  function checkPath(name, text) {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) fail(JSON.stringify(name) + ": URLs are for the command line only.");
    var expanded = text.replace(/^~(?=[\\/]|$)/, "/home");
    if (!isAbsolute(expanded)) fail(JSON.stringify(name) + " must be an absolute path, not " + JSON.stringify(text) + ".");
  }

  /* The parameters without the mockups' own hint, or a 4xx failure */
  M.validate = function (op, body) {
    var schema = root.WGSMSchema && root.WGSMSchema.ops[op];
    if (!schema) M.fail(404, "unknown_op", "No operation " + JSON.stringify(op) + "; see /v1/ops.");
    if (!body || typeof body !== "object" || Array.isArray(body)) fail("The parameters must be a JSON object.");
    var missing = schema.required.filter(function (name) {
      return !Object.prototype.hasOwnProperty.call(body, name);
    });
    if (missing.length) fail("Missing required parameter(s): " + missing.join(", ") + ".");
    Object.keys(body).forEach(function (name) {
      if (name === "_mock") return;
      if (!Object.prototype.hasOwnProperty.call(schema.fields, name)) {
        fail("Unknown parameter " + JSON.stringify(name) + "; " + op + " takes: " + Object.keys(schema.fields).join(", ") + ".");
      }
      checkOne(name, schema.fields[name], body[name]);
    });
    return body;
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
