import fs from "fs";
import path from "path";

export function loadBenchmarkPrompt(): string {
  const root = process.cwd();
  return fs.readFileSync(
    path.join(root, "skills", "benchmark-prompt.md"),
    "utf-8"
  );
}
