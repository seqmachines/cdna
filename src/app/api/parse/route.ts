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
      const res = await fetch(urlField);
      if (!res.ok) {
        return NextResponse.json(
          { error: `Failed to fetch URL: ${res.status}` },
          { status: 400, headers: cors }
        );
      }
      const buffer = Buffer.from(await res.arrayBuffer());
      const contentType = res.headers.get("content-type") || "";

      const urlPath = new URL(urlField).pathname;
      const fileName = urlPath.split("/").pop() || "document";

      const markdown = await parseProtocol(urlField, {
        fileData: buffer,
        fileName: contentType.includes("text") ? undefined : fileName,
        text: contentType.includes("text")
          ? new TextDecoder().decode(buffer)
          : undefined,
      }, modelField || undefined);
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
