import { getBot } from "@/lib/bot";
import { after } from "next/server";

export const maxDuration = 300;

export async function POST(req: Request) {
  const bot = getBot();
  return bot.webhooks.slack(req, {
    waitUntil: (p) => after(() => p),
  });
}
