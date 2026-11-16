import { cp, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pdfjsRoot = path.join(frontendRoot, "node_modules", "pdfjs-dist");
const publicRoot = path.join(frontendRoot, "public", "pdfjs");

await mkdir(publicRoot, { recursive: true });
await cp(path.join(pdfjsRoot, "cmaps"), path.join(publicRoot, "cmaps"), { recursive: true });
await cp(path.join(pdfjsRoot, "standard_fonts"), path.join(publicRoot, "standard_fonts"), { recursive: true });

console.log("Synced PDF.js cMaps and standard fonts into frontend/public/pdfjs.");
