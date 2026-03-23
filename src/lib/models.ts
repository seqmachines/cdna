import { google } from "@ai-sdk/google";

export const DEFAULT_MODEL = "google/gemini-3.1-pro-preview";

export interface ModelInfo {
  id: string;
  name: string;
}

interface GatewayModel {
  id: string;
  name?: string;
  type?: string;
  released?: number | string | null;
  created?: number | string | null;
  updated?: number | string | null;
}

interface GatewayModelsResponse {
  data?: GatewayModel[];
}

// --- Cached model list ---

let cachedGatewayModels: GatewayModel[] | null = null;
let cacheTime = 0;
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

function getModelTimestamp(model: GatewayModel): number {
  const candidates = [model.released, model.created, model.updated];

  for (const value of candidates) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }

    if (typeof value === "string") {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) {
        return numeric;
      }

      const parsed = Date.parse(value);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
  }

  return 0;
}

async function fetchGatewayModels(): Promise<GatewayModel[]> {
  if (cachedGatewayModels && Date.now() - cacheTime < CACHE_TTL) {
    return cachedGatewayModels;
  }

  const res = await fetch("https://ai-gateway.vercel.sh/v1/models");
  if (!res.ok) {
    if (cachedGatewayModels) return cachedGatewayModels; // stale cache better than nothing
    throw new Error(`Failed to fetch models: ${res.status}`);
  }

  const data = (await res.json()) as GatewayModelsResponse | GatewayModel[];
  const models = Array.isArray(data) ? data : data.data || [];

  cachedGatewayModels = models;
  cacheTime = Date.now();
  return models;
}

export async function fetchModels(): Promise<ModelInfo[]> {
  const models = await fetchGatewayModels();

  return models
    .filter((m) => m.type === "language")
    .map((m) => ({
      id: m.id,
      name: m.name || m.id,
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

export async function fetchTopGoogleGeminiModels(
  limit = 5
): Promise<string[]> {
  const models = await fetchGatewayModels();

  const ids = models
    .filter((model) => {
      return model.type === "language" && model.id.startsWith("google/gemini-");
    })
    .sort((a, b) => {
      const timeDiff = getModelTimestamp(b) - getModelTimestamp(a);
      if (timeDiff !== 0) return timeDiff;
      return b.id.localeCompare(a.id);
    })
    .map((model) => model.id)
    .filter((id, index, all) => all.indexOf(id) === index)
    .slice(0, limit);

  return ids.length > 0 ? ids : [DEFAULT_MODEL];
}

// --- Fuzzy match model from free text ---

const MODEL_KEYWORDS: [string[], string][] = [
  [["opus"], "anthropic/claude-opus"],
  [["sonnet", "claude"], "anthropic/claude-sonnet"],
  [["haiku"], "anthropic/claude-haiku"],
  [["flash"], "google/gemini-flash"],
  [["gemini", "google"], "google/gemini"],
  [["gpt", "openai"], "openai/gpt"],
  [["o1"], "openai/o1"],
  [["o3"], "openai/o3"],
  [["deepseek"], "deepseek/deepseek"],
  [["mistral"], "mistral/mistral"],
  [["llama", "meta"], "meta/llama"],
];

export async function resolveModelFromText(
  text: string
): Promise<string> {
  const lower = text.toLowerCase();

  for (const [keywords, prefix] of MODEL_KEYWORDS) {
    if (keywords.some((kw) => lower.includes(kw))) {
      const models = await fetchGatewayModels();
      const matches = models
        .filter((m) => m.type === "language" && m.id.startsWith(prefix))
        .sort((a, b) => {
          const timeDiff = getModelTimestamp(b) - getModelTimestamp(a);
          if (timeDiff !== 0) return timeDiff;
          return b.id.localeCompare(a.id);
        });

      if (matches.length > 0) {
        return matches[0].id;
      }
    }
  }

  return DEFAULT_MODEL;
}

// --- Resolve model ID to AI SDK model object ---

export function resolveModel(modelId: string): ReturnType<typeof google> | string {
  // Google models: use @ai-sdk/google directly (works with GOOGLE_GENERATIVE_AI_API_KEY)
  if (modelId.startsWith("google/")) {
    return google(modelId.replace("google/", ""));
  }

  // Other providers: pass as plain string for AI Gateway routing
  return modelId;
}
