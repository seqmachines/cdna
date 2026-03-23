/**
 * One-time migration: read protocol .md files from protocols/ and write to Firestore.
 *
 * Usage:
 *   GOOGLE_APPLICATION_CREDENTIALS=path/to/sa.json npx tsx scripts/migrate-to-firestore.ts
 */

import { readdirSync, readFileSync } from "fs";
import { join } from "path";
import matter from "gray-matter";
import { initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

const PROTOCOLS_DIR = join(__dirname, "..", "protocols");
const COLLECTION = "protocols";

async function main() {
  const app = initializeApp();
  const db = getFirestore(app);

  const files = readdirSync(PROTOCOLS_DIR).filter((f) => f.endsWith(".md"));
  console.log(`Found ${files.length} protocol files`);

  for (const file of files) {
    const raw = readFileSync(join(PROTOCOLS_DIR, file), "utf-8");
    const { data: frontmatter, content } = matter(raw);

    const slug =
      frontmatter.slug || file.replace(/\.md$/, "");
    const title = frontmatter.title || slug;
    const source = frontmatter.source || "";
    const date = frontmatter.date || new Date().toISOString();

    const protocol = {
      slug,
      title,
      source,
      content: raw, // preserve frontmatter + content as-is
      createdAt: date,
      updatedAt: date,
    };

    await db.collection(COLLECTION).doc(slug).set(protocol);
    console.log(`  Migrated: ${slug}`);
  }

  console.log("Done!");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
