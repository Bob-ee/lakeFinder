#!/usr/bin/env node
// CLI wrapper for the rules engine, used by the Python pipeline's `classify`
// stage (see docs/data-contract.md "Rules engine API").
//
//   node rules/engine/cli.js --rules rules/rules.json [--mac-loaded true|false] < input.json > output.json
//
// stdin:  {"lakes": [...], "restrictions": {"<lake_id>": [records]}}
// stdout: JSON array of {id, verdict, reasons, flags} (see evaluateLake).
//
// Reads stdin fully once and evaluates every lake in memory (no per-lake
// I/O), so it stays fast for tens of thousands of lakes.

import { readFileSync } from "node:fs";
import { loadRules, evaluateAll } from "./index.js";

class CliError extends Error {}

function parseArgs(argv) {
  const args = { rules: null, macLoaded: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--rules") {
      args.rules = argv[++i];
    } else if (arg.startsWith("--rules=")) {
      args.rules = arg.slice("--rules=".length);
    } else if (arg === "--mac-loaded") {
      args.macLoaded = argv[++i];
    } else if (arg.startsWith("--mac-loaded=")) {
      args.macLoaded = arg.slice("--mac-loaded=".length);
    } else {
      throw new CliError(`unrecognized argument "${arg}"`);
    }
  }
  return args;
}

function parseBooleanFlag(raw, flagName) {
  if (raw === null) return undefined;
  if (raw === "true") return true;
  if (raw === "false") return false;
  throw new CliError(`--${flagName} must be "true" or "false", got "${raw}"`);
}

function readJsonFile(path, label) {
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch (err) {
    throw new CliError(`could not read ${label} "${path}": ${err.message}`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new CliError(`${label} "${path}" is not valid JSON: ${err.message}`);
  }
}

function readStdinJson() {
  let raw;
  try {
    raw = readFileSync(0, "utf8");
  } catch (err) {
    throw new CliError(`could not read stdin: ${err.message}`);
  }
  if (!raw || raw.trim().length === 0) {
    throw new CliError('stdin was empty; expected JSON {"lakes": [...], "restrictions": {...}}');
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new CliError(`stdin is not valid JSON: ${err.message}`);
  }
}

function run(argv) {
  const args = parseArgs(argv);
  if (!args.rules) {
    throw new CliError('missing required argument "--rules <path>"');
  }
  const macLoaded = parseBooleanFlag(args.macLoaded, "mac-loaded");

  const rulesJson = readJsonFile(args.rules, "rules file");
  let rules;
  try {
    rules = loadRules(rulesJson);
  } catch (err) {
    throw new CliError(`invalid rules file "${args.rules}": ${err.message}`);
  }

  const input = readStdinJson();
  if (input == null || typeof input !== "object" || Array.isArray(input)) {
    throw new CliError('stdin must be a JSON object of the form {"lakes": [...], "restrictions": {...}}');
  }
  const { lakes, restrictions } = input;
  if (!Array.isArray(lakes)) {
    throw new CliError('stdin "lakes" must be an array');
  }
  if (restrictions != null && (typeof restrictions !== "object" || Array.isArray(restrictions))) {
    throw new CliError('stdin "restrictions" must be an object keyed by lake id');
  }

  const options = macLoaded === undefined ? {} : { mac_record_loaded: macLoaded };

  let results;
  try {
    results = evaluateAll(lakes, restrictions || {}, rules, options);
  } catch (err) {
    throw new CliError(`evaluation failed: ${err.message}`);
  }

  process.stdout.write(JSON.stringify(results));
}

try {
  run(process.argv.slice(2));
} catch (err) {
  if (err instanceof CliError) {
    process.stderr.write(`rules/engine/cli.js: ${err.message}\n`);
  } else {
    process.stderr.write(`rules/engine/cli.js: unexpected error: ${err.stack || err.message}\n`);
  }
  process.exit(1);
}
