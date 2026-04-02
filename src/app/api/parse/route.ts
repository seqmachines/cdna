import { NextResponse } from "next/server";
import { parseProtocol } from "@/lib/parse-protocol";
import { corsHeaders, handleCorsOptions } from "@/lib/cors";

export const maxDuration = 300;

export function OPTIONS(req: Request) {
  return handleCorsOptions(req);
}

export async function POST(req: Request) {
  const cors = corsHeaders(req);

  try {
    const formData = await req.formData();
    const urlField = formData.get("url") as string | null;
    const textField = formData.get("text") as string | null;
    const fileField = formData.get("file");
    const modelField = formData.get("model") as string | null;

    if (fileField && fileField instanceof File) {
      const buffer = Buffer.from(await fileField.arrayBuffer());
      const markdown = await parseProtocol(fileField.name, {
        fileData: buffer,
        fileName: fileField.name,
      }, modelField || undefined);
      return NextResponse.json({ markdown }, { headers: cors });
    }

    if (urlField) {
      let buffer: Buffer | undefined;
      let contentType = "";
      let fileName = "";

      try {
        const res = await fetch(urlField);
        if (res.ok) {
          contentType = res.headers.get("content-type") || "";
          buffer = Buffer.from(await res.arrayBuffer());
          fileName = new URL(urlField).pathname.split("/").pop() || "document";
        }
      } catch {
        // URL inaccessible — LLM will use search tools
      }

      if (buffer) {
        const isPdf = contentType.includes("pdf") || urlField.toLowerCase().endsWith(".pdf");
        const isText = contentType.includes("text") && !isPdf;

        const markdown = await parseProtocol(urlField, {
          fileData: isText ? undefined : buffer,
          fileName: isText ? undefined : fileName,
          text: isText ? new TextDecoder().decode(buffer) : undefined,
        }, modelField || undefined);
        return NextResponse.json({ markdown }, { headers: cors });
      }

      // URL failed — tell LLM to search
      const protocolHint = formData.get("protocol_name") as string | null;
      const fallbackText = `Could not access ${urlField}. ` +
        `Use the web_search tool to find the protocol` +
        (protocolHint ? ` "${protocolHint}"` : "") +
        ` and extract its details.`;

      const markdown = await parseProtocol(urlField, { text: fallbackText }, modelField || undefined);
      return NextResponse.json({ markdown }, { headers: cors });
    }

    if (textField) {
      const markdown = await parseProtocol("pasted text", { text: textField }, modelField || undefined);
      return NextResponse.json({ markdown }, { headers: cors });
    }

    return NextResponse.json(
      { error: "Provide a URL, file, or pasted text" },
      { status: 400, headers: cors }
    );
  } catch (err) {
    console.error("Parse error:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Parse failed" },
      { status: 500, headers: cors }
    );
  }
}
