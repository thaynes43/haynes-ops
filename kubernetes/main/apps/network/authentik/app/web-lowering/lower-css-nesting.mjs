#!/usr/bin/env node
/**
 * lower-css-nesting.mjs — PLAN-042 Option A (haynesnetwork).
 *
 * Post-processes authentik's served web assets so that NO native CSS nesting
 * reaches the browser. Old WebKit (iOS/iPadOS ~16.6–18.3.x, macOS Safari ~17.6)
 * crashes its WebContent process on StyleRuleNestedDeclarations
 * (WebKit bug #290102); authentik >= 2025.12 ships native nesting both in the
 * standalone CSS bundles AND in esbuild-embedded component styles inside the JS
 * bundles (upstream goauthentik/authentik#19814, RCA comment 2026-06-30).
 *
 * Method (the RCA author's verified recipe, applied as a post-process):
 *   - every *.css file: lightningcss transform, targets safari >= 15
 *     (+ Features.Nesting forced) -> flat selectors, zero nesting.
 *   - every *.js/*.mjs file: parse with acorn; for every string literal and
 *     every expression-free template literal whose value looks like CSS and
 *     contains a nesting marker, run the same lightningcss lowering and splice
 *     the lowered CSS back (template literals stay template literals so tagged
 *     templates -- Lit css`` -- keep their semantics).
 *   - delete any precompressed *.br / *.gz siblings of rewritten files so the
 *     server can never serve a stale compressed variant.
 *
 * Exit policy: exits 1 if the final scan still finds nesting markers in any
 * CSS file or any extracted CSS string (partial coverage does NOT fix the
 * crash). A failing init container leaves the Deployment on its previous
 * ReplicaSet -- login stays up on the old assets and the failure is loud.
 */
import { readdirSync, readFileSync, writeFileSync, existsSync, unlinkSync } from "node:fs";
import { join, extname, relative } from "node:path";
import { transform, Features } from "lightningcss";
import { parse } from "acorn";

const ROOT = process.argv[2];
if (!ROOT) {
  console.error("usage: lower-css-nesting.mjs <dist-dir>");
  process.exit(2);
}

// safari >= 15 (major << 16 | minor << 8) — pre-native-nesting target.
const TARGETS = { safari: 15 << 16 };

// Nesting marker: '&' used as a nesting selector. Requires whitespace before a
// bare element name so URL query strings ('?a=1&b=2') don't false-positive.
const MARKER_SRC = String.raw`&(?=:|\.|\[|&|\s*\{|\s+[.#[:a-zA-Z*>+~])`;
const markerCount = (s) => (s.match(new RegExp(MARKER_SRC, "g")) || []).length;

// CSS-ish = has a rule block, a nesting marker, AND at least one
// `property: value` declaration (excludes e.g. Intl locale strings like
// "{0}, & {1}" which carry '&' + braces but no declarations).
const looksLikeCss = (s) =>
  s.includes("{") &&
  s.includes("}") &&
  new RegExp(MARKER_SRC).test(s) &&
  /[-a-zA-Z]+\s*:\s*[^;{}]+[;}]/.test(s);

function lowerCss(code, filename) {
  const res = transform({
    filename,
    code: Buffer.from(code),
    targets: TARGETS,
    include: Features.Nesting,
    minify: true,
    errorRecovery: false,
  });
  return res.code.toString();
}

/** Try to lower a candidate CSS string; null = not CSS / failed (leave as-is). */
function tryLower(s, filename) {
  try {
    return lowerCss(s, filename);
  } catch {
    return null;
  }
}

/** Generic AST walk (object-graph recursion; robust across node types). */
function walkAst(node, visit) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const c of node) walkAst(c, visit);
    return;
  }
  if (typeof node.type === "string") visit(node);
  for (const key of Object.keys(node)) {
    if (key === "type" || key === "start" || key === "end") continue;
    walkAst(node[key], visit);
  }
}

const escTemplate = (s) => s.replace(/\\/g, "\\\\").replace(/`/g, "\\`").replace(/\$\{/g, "\\${");

const stats = { cssFiles: 0, cssChanged: 0, jsFiles: 0, jsChanged: 0, literalsLowered: 0, skippedLiterals: [], parseErrors: [], compressedDeleted: 0 };

function processJs(code, file) {
  let ast = null;
  for (const sourceType of ["module", "script"]) {
    try {
      ast = parse(code, { ecmaVersion: "latest", sourceType });
      break;
    } catch {
      /* try next */
    }
  }
  if (!ast) {
    stats.parseErrors.push(file);
    return null;
  }
  const edits = [];
  walkAst(ast, (node) => {
    if (node.type === "Literal" && typeof node.value === "string" && looksLikeCss(node.value)) {
      const lowered = tryLower(node.value, file);
      if (lowered === null) stats.skippedLiterals.push(`${file}@${node.start}`);
      else if (lowered !== node.value) edits.push({ start: node.start, end: node.end, text: JSON.stringify(lowered) });
    } else if (node.type === "TemplateLiteral") {
      if (node.expressions.length === 0 && node.quasis.length === 1) {
        const cooked = node.quasis[0].value.cooked;
        if (typeof cooked === "string" && looksLikeCss(cooked)) {
          const lowered = tryLower(cooked, file);
          if (lowered === null) stats.skippedLiterals.push(`${file}@${node.start}`);
          else if (lowered !== cooked) edits.push({ start: node.start, end: node.end, text: "`" + escTemplate(lowered) + "`" });
        }
      } else {
        // Interpolated template: cannot transform safely; flag if CSS-ish.
        for (const q of node.quasis) {
          const cooked = q.value.cooked;
          if (typeof cooked === "string" && looksLikeCss(cooked)) {
            stats.skippedLiterals.push(`${file}@${node.start} (interpolated)`);
            break;
          }
        }
      }
    }
  });
  if (edits.length === 0) return null;
  edits.sort((a, b) => b.start - a.start);
  let out = code;
  for (const e of edits) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  stats.literalsLowered += edits.length;
  return out;
}

function* files(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) yield* files(p);
    else if (entry.isFile()) yield p;
  }
}

function dropCompressedSiblings(p) {
  for (const ext of [".br", ".gz"]) {
    if (existsSync(p + ext)) {
      unlinkSync(p + ext);
      stats.compressedDeleted++;
    }
  }
}

for (const p of files(ROOT)) {
  const ext = extname(p);
  const rel = relative(ROOT, p);
  if (ext === ".css") {
    stats.cssFiles++;
    const src = readFileSync(p, "utf-8");
    let lowered;
    try {
      lowered = lowerCss(src, rel);
    } catch (e) {
      stats.parseErrors.push(`${rel}: ${e.message}`);
      continue;
    }
    if (lowered !== src) {
      writeFileSync(p, lowered);
      dropCompressedSiblings(p);
      stats.cssChanged++;
    }
  } else if (ext === ".js" || ext === ".mjs") {
    stats.jsFiles++;
    const src = readFileSync(p, "utf-8");
    const out = processJs(src, rel);
    if (out !== null) {
      writeFileSync(p, out);
      dropCompressedSiblings(p);
      stats.jsChanged++;
    }
  }
}

// ---- final scan: zero nesting markers must remain in served CSS -------------
const residual = [];
for (const p of files(ROOT)) {
  const ext = extname(p);
  const rel = relative(ROOT, p);
  if (ext === ".css") {
    const src = readFileSync(p, "utf-8");
    const n = markerCount(src);
    if (n > 0) residual.push(`${rel}: ${n} markers`);
  } else if (ext === ".js" || ext === ".mjs") {
    const src = readFileSync(p, "utf-8");
    let ast = null;
    for (const sourceType of ["module", "script"]) {
      try {
        ast = parse(src, { ecmaVersion: "latest", sourceType });
        break;
      } catch {
        /* try next */
      }
    }
    if (!ast) {
      residual.push(`${rel}: UNPARSEABLE`);
      continue;
    }
    walkAst(ast, (node) => {
      const check = (s, where) => {
        if (typeof s === "string" && looksLikeCss(s)) {
          const n = markerCount(s);
          residual.push(`${rel}@${where}: ${n} markers in embedded CSS`);
        }
      };
      if (node.type === "Literal" && typeof node.value === "string") check(node.value, node.start);
      else if (node.type === "TemplateLiteral") for (const q of node.quasis) check(q.value.cooked, node.start);
    });
  }
}

console.log(JSON.stringify({ ...stats, skippedLiterals: stats.skippedLiterals.slice(0, 20), parseErrors: stats.parseErrors.slice(0, 20), residualCount: residual.length, residual: residual.slice(0, 40) }, null, 2));

if (residual.length > 0) {
  console.error(`FAIL: ${residual.length} residual nesting site(s) — partial coverage does not fix WebKit #290102.`);
  process.exit(1);
}
console.log("OK: zero nesting markers in served CSS (files + JS-embedded).");
