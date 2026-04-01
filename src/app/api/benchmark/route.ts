import { NextResponse } from "next/server";
import { parseBenchmark } from "@/lib/parse-benchmark";
import { corsHeaders, handleCorsOptions } from "@/lib/cors";
import { findSupplementaryUrls, hasValidSequence } from "@/lib/supplementary";
import { googleSearch } from "@/lib/google-search";
import { tryFetch, protocolHintFromUrl, type FetchedContent } from "@/lib/fetch-content";

export const maxDuration = 300;

export function OPTIONS(req: Request) {
  return handleCorsOptions(req);
}

function sendToLLM(content: FetchedContent, model: string | undefined) {
  if (content.isPdf) {
    return parseBenchmark(content.url, {
      fileData: content.buffer,
      fileName: content.fileName,
    }, model);
  }
  return parseBenchmark(content.url, { text: content.text }, model);
}

function sendCombinedToLLM(
  paper: FetchedContent,
  supp: FetchedContent,
  model: string | undefined,
) {
  if (supp.isPdf) {
    return parseBenchmark(supp.url, {
      fileData: supp.buffer,
      fileName: supp.fileName,
      text: paper.text
        ? `--- PAPER CONTENT ---\n\n${paper.text}\n\n--- END PAPER CONTENT ---\n\nThe supplementary PDF is attached. Extract the library structure from the supplementary material.`
        : "The supplementary PDF is attached. Extract the library structure from it.",
    }, model);
  }

  if (paper.isPdf) {
    return parseBenchmark(paper.url, {
      fileData: paper.buffer,
      fileName: paper.fileName,
      text: supp.text ? `--- SUPPLEMENTARY MATERIAL ---\n\n${supp.text}` : undefined,
    }, model);
  }

  return parseBenchmark(paper.url, {
    text: (paper.text || "") + "\n\n--- SUPPLEMENTARY MATERIAL ---\n\n" + (supp.text || ""),
  }, model);
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

    if (fileField && fileField instanceof File) {
      const buffer = Buffer.from(await fileField.arrayBuffer());
      const data = await parseBenchmark(fileField.name, {
        fileData: buffer,
        fileName: fileField.name,
      }, model);
      return NextResponse.json(data, { headers: cors });
    }

    if (urlField) {
      const protocolHint = (formData.get("protocol_name") as string | null)
        || protocolHintFromUrl(urlField);

      const sourceInfo: Record<string, unknown> = {
        primary_url: urlField,
        step_reached: 1,
        fallback_used: false,
      };

      // STEP 1: Try input URL directly
      console.log(`  Step 1: Fetching ${urlField.slice(0, 80)}...`);
      const step1Content = await tryFetch(urlField);

      if (step1Content) {
        const data = await sendToLLM(step1Content, model);
        if (hasValidSequence(data.result)) {
          return NextResponse.json({ ...data, source_info: sourceInfo }, { headers: cors });
        }
        console.log("  Step 1 failed: no valid sequence");
      } else {
        console.log("  Step 1 failed: could not fetch");
      }

      // STEP 2: Google Search "{protocol} sequencing protocol"
      const q2 = `${protocolHint} sequencing protocol`;
      console.log(`  Step 2: Searching "${q2}"`);
      sourceInfo.step_reached = 2;

      for (const url of await googleSearch(q2)) {
        const content = await tryFetch(url);
        if (!content) continue;
        const data = await sendToLLM(content, model);
        if (hasValidSequence(data.result)) {
          sourceInfo.fallback_used = true;
          sourceInfo.resolved_url = url;
          sourceInfo.search_query = q2;
          return NextResponse.json({ ...data, source_info: sourceInfo }, { headers: cors });
        }
      }
      console.log("  Step 2 failed");

      // STEP 3: Google Search "{protocol} paper"
      const q3 = `${protocolHint} paper`;
      console.log(`  Step 3: Searching "${q3}"`);
      sourceInfo.step_reached = 3;

      let paperContent: FetchedContent | null = null;
      for (const url of await googleSearch(q3)) {
        const content = await tryFetch(url);
        if (!content) continue;
        if (!paperContent) paperContent = content;
        const data = await sendToLLM(content, model);
        if (hasValidSequence(data.result)) {
          sourceInfo.fallback_used = true;
          sourceInfo.resolved_url = url;
          sourceInfo.search_query = q3;
          return NextResponse.json({ ...data, source_info: sourceInfo }, { headers: cors });
        }
      }
      console.log("  Step 3 failed");

      // STEP 4: Parse paper HTML for supplementary links
      console.log("  Step 4: Looking for supplementary links...");
      sourceInfo.step_reached = 4;

      const bestPaper = paperContent || step1Content;

      if (bestPaper?.text) {
        const suppUrls = findSupplementaryUrls(bestPaper.text, bestPaper.url);
        console.log(`  Step 4: Found ${suppUrls.length} supplementary URLs`);

        for (const suppUrl of suppUrls.slice(0, 3)) {
          const suppContent = await tryFetch(suppUrl);
          if (!suppContent) continue;

          const data = await sendCombinedToLLM(bestPaper, suppContent, model);
          if (hasValidSequence(data.result)) {
            sourceInfo.fallback_used = true;
            sourceInfo.supplementary_url = suppUrl;
            return NextResponse.json({ ...data, source_info: sourceInfo }, { headers: cors });
          }
        }
      } else if (bestPaper?.isPdf) {
        console.log("  Step 4: Paper is PDF — cannot extract supplementary links");
      } else {
        console.log("  Step 4: No paper content available");
      }
      console.log("  Step 4 failed");

      // STEP 5: All failed
      sourceInfo.step_reached = 5;
      sourceInfo.error = "All steps failed";
      console.log("  Step 5: Reporting failure");

      return NextResponse.json(
        { result: null, raw: "", source_info: sourceInfo },
        { headers: cors },
      );
    }

    if (textField) {
      const data = await parseBenchmark("pasted text", { text: textField }, model);
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
