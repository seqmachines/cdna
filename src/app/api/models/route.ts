import { NextResponse } from "next/server";
import { fetchModels } from "@/lib/models";
import { corsHeaders, handleCorsOptions } from "@/lib/cors";

export function OPTIONS(req: Request) {
  return handleCorsOptions(req);
}

export async function GET(req: Request) {
  const cors = corsHeaders(req);

  try {
    const models = await fetchModels();
    return NextResponse.json(models, { headers: cors });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to fetch models" },
      { status: 500, headers: cors }
    );
  }
}
