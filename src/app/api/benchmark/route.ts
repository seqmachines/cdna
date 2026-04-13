import { NextResponse } from "next/server";
import { parseBenchmark } from "@/lib/parse-benchmark";
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
    const model = modelField || undefined;
    const useToolsField = formData.get("use_tools") as string | null;
    const useTools = useToolsField === "true" || useToolsField === "1";

    if (fileField && fileField instanceof File) {
      const buffer = Buffer.from(await fileField.arrayBuffer());
      const data = await parseBenchmark(fileField.name, {
        fileData: buffer,
        fileName: fileField.name,
      }, model, useTools);
      return NextResponse.json(data, { headers: cors });
    }

    if (urlField) {
      const res = await fetch(urlField, {
        headers: { "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)" },
        redirect: "follow",
        signal: AbortSignal.timeout(30000),
      });

      if (!res.ok) {
        return NextResponse.json(
          { error: `Failed to fetch: ${res.status}` },
          { status: 400, headers: cors }
        );
      }

      const contentType = res.headers.get("content-type") || "";
      const buffer = Buffer.from(await res.arrayBuffer());
      const isPdf = contentType.includes("pdf") || urlField.toLowerCase().endsWith(".pdf");
      const isText = contentType.includes("text") && !isPdf;
      const fileName = new URL(urlField).pathname.split("/").pop() || "document";

      const data = await parseBenchmark(urlField, {
        fileData: isText ? undefined : buffer,
        fileName: isText ? undefined : fileName,
        text: isText ? new TextDecoder().decode(buffer) : undefined,
      }, model, useTools);

      return NextResponse.json(data, { headers: cors });
    }

    if (textField) {
      const data = await parseBenchmark("pasted text", { text: textField }, model, useTools);
      return NextResponse.json(data, { headers: cors });
    }

    return NextResponse.json(
      { error: "Provide a URL, file, or pasted text" },
      { status: 400, headers: cors }
    );
  } catch (err) {
    console.error("Benchmark parse error:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Benchmark parse failed" },
      { status: 500, headers: cors }
    );
  }
}
