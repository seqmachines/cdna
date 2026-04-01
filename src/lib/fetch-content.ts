export interface FetchedContent {
  buffer: Buffer;
  contentType: string;
  isPdf: boolean;
  isText: boolean;
  text: string | undefined;
  fileName: string;
  url: string;
}

export async function tryFetch(url: string): Promise<FetchedContent | null> {
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)" },
      redirect: "follow",
      signal: AbortSignal.timeout(30000),
    });
    if (!r.ok) return null;
    const ct = r.headers.get("content-type") || "";
    const buf = Buffer.from(await r.arrayBuffer());
    const isPdf = ct.includes("pdf") || url.toLowerCase().endsWith(".pdf");
    const isText = ct.includes("text") && !isPdf;
    return {
      buffer: buf,
      contentType: ct,
      isPdf,
      isText,
      text: isText ? new TextDecoder().decode(buf) : undefined,
      fileName: url.split("/").pop() || "document",
      url,
    };
  } catch {
    return null;
  }
}

export function protocolHintFromUrl(url: string): string {
  return url
    .replace(/https?:\/\//, "")
    .replace(/[/\-_.,()]/g, " ")
    .replace(/\b(com|org|www|pdf|html|articles?|doi|content|abstract|fulltext|image|upload|support|documents?)\b/gi, "")
    .trim()
    .slice(0, 80);
}
