import { tool } from "ai";
import { z } from "zod";

export const webSearchTool = tool({
  description:
    "Search the web using Google. Use when the provided content doesn't contain " +
    "the information you need — e.g. adapter/primer sequences are missing, or you " +
    "need to find supplementary materials or protocol PDFs.",
  inputSchema: z.object({
    query: z.string().describe("Search query"),
  }),
  execute: async ({ query }) => {
    const apiKey = process.env.GOOGLE_SEARCH_API_KEY;
    const cx = process.env.GOOGLE_SEARCH_ENGINE_ID;
    if (!apiKey || !cx) return { error: "Search not configured", results: [] };

    try {
      const url = `https://www.googleapis.com/customsearch/v1?q=${encodeURIComponent(query)}&key=${apiKey}&cx=${cx}&num=5`;
      const res = await fetch(url);
      if (!res.ok) return { error: `HTTP ${res.status}`, results: [] };
      const data = await res.json();
      return {
        results: (data.items || []).map((item: { title: string; link: string; snippet: string }) => ({
          title: item.title,
          url: item.link,
          snippet: item.snippet,
        })),
      };
    } catch (err) {
      return { error: String(err), results: [] };
    }
  },
});

export const fetchUrlTool = tool({
  description:
    "Fetch the content of a URL. Use to read pages found via web_search. " +
    "Returns text content. PDFs are automatically converted to text.",
  inputSchema: z.object({
    url: z.string().url().describe("URL to fetch"),
  }),
  execute: async ({ url }) => {
    try {
      const res = await fetch(url, {
        headers: { "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)" },
        redirect: "follow",
        signal: AbortSignal.timeout(30000),
      });
      if (!res.ok) return { error: `HTTP ${res.status}`, content: null };

      const ct = res.headers.get("content-type") || "";
      const isPdf = ct.includes("pdf") || url.toLowerCase().endsWith(".pdf");

      if (isPdf) {
        const { pdfToText } = await import("./pdf-to-text");
        const buffer = Buffer.from(await res.arrayBuffer());
        const text = await pdfToText(buffer);
        return { content: text.slice(0, 50000), url, was_pdf: true };
      }

      const buffer = await res.arrayBuffer();
      const text = new TextDecoder().decode(buffer);
      return {
        content: text.length > 50000 ? text.slice(0, 50000) + "\n[Truncated]" : text,
        url,
        was_pdf: false,
      };
    } catch (err) {
      return { error: String(err), content: null };
    }
  },
});
