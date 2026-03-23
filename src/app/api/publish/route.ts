import { NextResponse } from "next/server";
import { saveProtocol } from "@/lib/protocols";
import { corsHeaders, handleCorsOptions } from "@/lib/cors";

export function OPTIONS(req: Request) {
  return handleCorsOptions(req);
}

export async function POST(req: Request) {
  const cors = corsHeaders(req);

  try {
    const { slug, title, source, content } = await req.json();

    if (!slug || !content) {
      return NextResponse.json(
        { error: "slug and content are required" },
        { status: 400, headers: cors }
      );
    }

    const protocol = await saveProtocol(
      slug,
      title || "Untitled",
      source || "",
      content
    );

    return NextResponse.json({ slug: protocol.slug }, { headers: cors });
  } catch (err) {
    console.error("Publish error:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Publish failed" },
      { status: 500, headers: cors }
    );
  }
}
