import fs from "fs";
import path from "path";

export function loadSkill() {
  const root = process.cwd();
  const skillMd = fs.readFileSync(
    path.join(root, "skills", "SKILL.md"),
    "utf-8"
  );
  const exampleMd = fs.readFileSync(
    path.join(root, "skills", "references", "example-output.md"),
    "utf-8"
  );
  return { skillMd, exampleMd };
}
