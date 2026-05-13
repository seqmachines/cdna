import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const TEXT_EXTENSIONS = new Set([".txt", ".md", ".csv", ".tsv"]);

export interface PreparedProtocolInput {
  text?: string;
  fileData?: Buffer;
  fileName?: string;
}

export function isPdfInput(fileName = "", contentType = "") {
  return contentType.toLowerCase().includes("pdf") || fileName.toLowerCase().endsWith(".pdf");
}

export function isTextInput(fileName = "", contentType = "") {
  const lowerContentType = contentType.toLowerCase();
  const ext = path.extname(fileName).toLowerCase();
  return lowerContentType.startsWith("text/") || TEXT_EXTENSIONS.has(ext);
}

export async function extractPdfText(buffer: Buffer, fileName = "protocol.pdf") {
  const dir = await mkdtemp(path.join(tmpdir(), "cdna-pdf-"));
  const pdfPath = path.join(dir, `${randomUUID()}-${path.basename(fileName || "protocol.pdf")}`);

  try {
    await writeFile(pdfPath, buffer);
    const scriptPath = path.join(process.cwd(), "scripts", "pdf_to_text.py");
    const { stdout } = await execFileAsync("python3", [scriptPath, pdfPath], {
      maxBuffer: 50 * 1024 * 1024,
    });
    return stdout;
  } catch (error) {
    const message = error instanceof Error ? error.message : "PDF text extraction failed";
    throw new Error(`PDF text extraction failed: ${message}`);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

export async function prepareProtocolInput(
  buffer: Buffer,
  fileName: string,
  contentType = ""
): Promise<PreparedProtocolInput> {
  if (isPdfInput(fileName, contentType)) {
    return { text: await extractPdfText(buffer, fileName) };
  }

  if (isTextInput(fileName, contentType)) {
    return { text: new TextDecoder().decode(buffer) };
  }

  return { fileData: buffer, fileName };
}
