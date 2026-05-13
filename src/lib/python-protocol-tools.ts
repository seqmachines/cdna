import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export interface PythonProtocolContext {
  inventory: {
    candidates: Array<{ sequence: string } & Record<string, unknown>>;
    source_spans: Record<string, unknown>;
  };
  prompt_block: string;
}

async function runProtocolSupport(args: string[]) {
  const scriptPath = path.join(process.cwd(), "scripts", "protocol_parse_support.py");
  const { stdout, stderr } = await execFileAsync("python3", [scriptPath, ...args], {
    maxBuffer: 50 * 1024 * 1024,
  });
  if (stderr.trim()) {
    throw new Error(stderr.trim());
  }
  return stdout;
}

export async function buildProtocolContext(text: string): Promise<PythonProtocolContext> {
  const dir = await mkdtemp(path.join(tmpdir(), "cdna-parse-"));
  const textPath = path.join(dir, `${randomUUID()}-protocol.txt`);

  try {
    await writeFile(textPath, text, "utf-8");
    const stdout = await runProtocolSupport(["build-context", textPath]);
    return JSON.parse(stdout) as PythonProtocolContext;
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

export async function finalizeProtocolFromModel(
  rawText: string,
  inventory: PythonProtocolContext["inventory"]
) {
  const dir = await mkdtemp(path.join(tmpdir(), "cdna-finalize-"));
  const rawPath = path.join(dir, `${randomUUID()}-raw.txt`);
  const inventoryPath = path.join(dir, `${randomUUID()}-inventory.json`);

  try {
    await writeFile(rawPath, rawText, "utf-8");
    await writeFile(inventoryPath, JSON.stringify(inventory), "utf-8");
    const stdout = await runProtocolSupport(["finalize", rawPath, inventoryPath]);
    return JSON.parse(stdout) as Record<string, unknown>;
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
