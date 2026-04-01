const SUPP_PATTERNS = [
  /href="([^"]*(?:supplement|supporting|supp|additional[_-]file)[^"]*)"/gi,
  /href="([^"]*(?:mmc\d+)[^"]*)"/gi,
  /href="([^"]*static-content\.springer\.com[^"]*)"/gi,
  /href="([^"]*\/suppl\/[^"]*)"/gi,
  /href="([^"]*(?:si[_-]?\d+)[^"]*)"/gi,
];

const USEFUL_EXT = /\.(pdf|html?|docx?|xlsx?|csv|txt)$/i;

export function findSupplementaryUrls(html: string, baseUrl: string): string[] {
  const urls: string[] = [];
  const base = new URL(baseUrl);

  for (const pattern of SUPP_PATTERNS) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(html)) !== null) {
      let url = match[1];
      if (url.startsWith("/")) {
        url = `${base.origin}${url}`;
      } else if (!url.startsWith("http")) {
        url = new URL(url, base).href;
      }
      if (USEFUL_EXT.test(url) || !url.match(/\.\w{1,5}$/)) {
        urls.push(url);
      }
    }
  }

  return [...new Set(urls)];
}

export function hasValidSequence(result: Record<string, unknown> | null): boolean {
  if (!result) return false;
  const seq = result.library_sequence;
  if (typeof seq !== "string" || seq.length < 20) return false;
  return /^[ACGTBUILRTXV]+$/i.test(seq);
}
