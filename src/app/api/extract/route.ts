import { NextResponse } from "next/server";
import { prepareProtocolInput } from "@/lib/extract-input-text";
import { extractProtocolStaged } from "@/lib/parse-protocol";
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
      const input = await prepareProtocolInput(buffer, fileField.name, fileField.type);
      const data = await extractProtocolStaged(fileField.name, input, modelField || undefined);
      return NextResponse.json(data, { headers: cors });
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
        // URL inaccessible.
      }

      if (buffer) {
        const input = await prepareProtocolInput(buffer, fileName, contentType);
        const data = await extractProtocolStaged(urlField, input, modelField || undefined);
        return NextResponse.json(data, { headers: cors });
      }

      return NextResponse.json(
        { error: `Failed to fetch URL: ${urlField}` },
        { status: 400, headers: cors }
      );
    }

    if (textField) {
      const data = await extractProtocolStaged(
        "pasted text",
        { text: textField },
        modelField || undefined
      );
      return NextResponse.json(data, { headers: cors });
    }

    return NextResponse.json(
      { error: "Provide a URL, file, or pasted text" },
      { status: 400, headers: cors }
    );
  } catch (err) {
    console.error("Extract error:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Extract failed" },
      { status: 500, headers: cors }
    );
  }
}
