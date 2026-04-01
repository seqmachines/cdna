export async function googleSearch(query: string, maxResults = 3): Promise<string[]> {
  const apiKey = process.env.GOOGLE_SEARCH_API_KEY;
  const cx = process.env.GOOGLE_SEARCH_ENGINE_ID;

  if (!apiKey || !cx) {
    console.log("  [search] Not configured — skipping");
    return [];
  }

  try {
    const url = `https://www.googleapis.com/customsearch/v1?q=${encodeURIComponent(query)}&key=${apiKey}&cx=${cx}&num=${maxResults}`;
    const res = await fetch(url);
    if (!res.ok) {
      console.log(`  [search] Google returned ${res.status}`);
      return [];
    }
    const data = await res.json();
    return (data.items || []).map((item: { link: string }) => item.link);
  } catch (err) {
    console.log(`  [search] Error: ${err}`);
    return [];
  }
}
