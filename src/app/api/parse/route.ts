import { NextResponse } from "next/server";
import { parseProtocol } from "@/lib/parse-protocol";
import { corsHeaders, handleCorsOptions } from "@/lib/cors";
import { googleSearch } from "@/lib/google-search";
import { tryFetch, protocolHintFromUrl } from "@/lib/fetch-content";

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
      let content = await tryFetch(urlField);

      if (!content) {
        console.log(`  Direct fetch failed for ${urlField}, searching...`);
        const hint = (formData.get("protocol_name") as string | null)
          || protocolHintFromUrl(urlField);
        for (const searchUrl of await googleSearch(`${hint} sequencing protocol`)) {
          content = await tryFetch(searchUrl);
          if (content) {
            console.log(`  Found via search: ${searchUrl}`);
            break;
          }
        }
      }

      if (!content) {
        return NextResponse.json(
          { error: "Failed to fetch URL and no alternatives found via search" },
          { status: 400, headers: cors }
        );
      }

      const markdown = await parseProtocol(content.url, {
        fileData: content.isPdf ? content.buffer : undefined,
        fileName: content.isPdf ? content.fileName : undefined,
        text: content.isText ? content.text : undefined,
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
