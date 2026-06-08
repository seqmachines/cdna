import { promises as fs } from "fs";
import { Chat, type Thread, type StateAdapter, type Lock } from "chat";
import {
  createSlackAdapter,
  type SlackAdapter,
  type SlackThreadId,
} from "@chat-adapter/slack";
import { generateText } from "ai";

/** No-op state adapter — all thread context is recovered from Slack message
 *  history via recoverThreadContext(), so no persistent state is needed. */
class NoopStateAdapter implements StateAdapter {
  private counter = 0;
  async connect() {}
  async disconnect() {}
  async subscribe() {}
  async unsubscribe() {}
  async isSubscribed() { return false; }
  async acquireLock(_threadId: string, _ttlMs: number): Promise<Lock | null> {
    return { token: String(++this.counter), threadId: _threadId, expiresAt: Date.now() + _ttlMs };
  }
  async releaseLock() {}
  async extendLock() { return true; }
  async forceReleaseLock() {}
  async get() { return null; }
  async set() {}
  async setIfNotExists() { return true; }
  async delete() {}
  async appendToList() {}
  async getList() { return []; }
}
import { parseProtocolMarkdown } from "./parse-protocol";
import { DEFAULT_MODEL, resolveModel } from "./models";

type ThreadStatus = "parsing" | "ready" | "error";

const PARSE_MODEL = "google/gemini-3.1-flash-lite-preview";

interface BotThreadState {
  parsedMarkdown?: string;
  selectedModel?: string;
  sourceUrl?: string;
  status?: ThreadStatus;
  title?: string;
}

let _bot: Chat<{ slack: SlackAdapter }, BotThreadState> | undefined;

function getBot() {
  if (!_bot) {
    _bot = new Chat({
      userName: "cdna",
      adapters: {
        slack: createSlackAdapter({
          botToken: process.env.SLACK_BOT_TOKEN!,
          signingSecret: process.env.SLACK_SIGNING_SECRET!,
        }),
      },
      state: new NoopStateAdapter(),
    });

    registerHandlers(_bot);
  }
  return _bot;
}

function splitMarkdown(text: string, maxLen = 3900): string[] {
  const sections = text.split(/(?=^## )/m);
  const chunks: string[] = [];
  let current = "";

  for (const section of sections) {
    if (current.length + section.length > maxLen && current) {
      chunks.push(current.trim());
      current = "";
    }
    current += section;
  }

  if (current.trim()) {
    chunks.push(current.trim());
  }

  return chunks;
}

function extractUrl(text: string): string | null {
  const slackMatch = text.match(/<(https?:\/\/[^>|]+)(?:\|[^>]*)?>/);
  if (slackMatch) return slackMatch[1];

  const plainMatch = text.match(/(https?:\/\/[^\s<>]+)/);
  return plainMatch ? plainMatch[1] : null;
}

function slugify(value: string) {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

  return slug || "protocol";
}

function extractTitle(markdown: string) {
  const titleMatch = markdown.match(/^#\s+(.+)$/m);
  return titleMatch ? titleMatch[1].trim() : "Untitled Protocol";
}

function isRevisionRequest(text: string) {
  return /\b(fix|revise|revision|update|correct|change|wrong|adjust|feedback|missing|typo|improve)\b/i.test(
    text
  );
}

async function recoverThreadContext(
  thread: Thread<BotThreadState>
): Promise<BotThreadState | null> {
  let sourceUrl: string | undefined;
  let selectedModel: string | undefined;
  let parsedMarkdown: string | undefined;
  let title: string | undefined;
  let hasBotMessages = false;
  let lastParseAt = 0;
  let lastMarkdownAt = 0;

  for await (const message of thread.allMessages) {
    const sentAt = message.metadata.dateSent.getTime();

    if (!sourceUrl && !message.author.isMe) {
      const url = extractUrl(message.text);
      if (url) {
        sourceUrl = url;
      }
    }

    if (message.author.isMe) {
      hasBotMessages = true;

      const parseModelMatch = message.text.match(
        /Parsing protocol with\s+`?(google\/gemini-[a-z0-9.-]+)`?/i
      );
      if (parseModelMatch) {
        selectedModel = parseModelMatch[1];
        lastParseAt = sentAt;
      }
    }

    for (const attachment of message.attachments) {
      const isMarkdownFile =
        attachment.type === "file" &&
        (!!attachment.name?.endsWith(".md") ||
          attachment.mimeType === "text/markdown" ||
          attachment.mimeType === "text/plain");

      if (!isMarkdownFile || !attachment.fetchData) {
        continue;
      }

      if (sentAt >= lastMarkdownAt) {
        const buffer = await attachment.fetchData();
        parsedMarkdown = buffer.toString("utf-8");
        title = extractTitle(parsedMarkdown);
        lastMarkdownAt = sentAt;
      }
    }
  }

  // Only return context if the bot has participated in this thread
  if (!hasBotMessages) {
    return null;
  }

  let status: ThreadStatus | undefined;
  if (parsedMarkdown) {
    status = "ready";
  } else if (lastParseAt > 0) {
    status = "parsing";
  }

  return {
    status,
    sourceUrl,
    selectedModel,
    parsedMarkdown,
    title,
  };
}

function getSlackThreadRef(thread: Thread<BotThreadState>) {
  const adapter = thread.adapter as SlackAdapter;
  return adapter.decodeThreadId(thread.id) as SlackThreadId;
}

async function postBroadcastMessage(
  thread: Thread<BotThreadState>,
  text: string
) {
  const token = process.env.SLACK_BOT_TOKEN;
  if (!token) {
    await thread.post(text);
    return;
  }

  const { channel, threadTs } = getSlackThreadRef(thread);
  const res = await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({
      channel,
      text,
      thread_ts: threadTs,
      reply_broadcast: true,
      unfurl_links: false,
      unfurl_media: false,
    }),
  });

  const data = (await res.json()) as { error?: string; ok?: boolean };
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `Slack post failed: ${res.status}`);
  }
}

async function postBroadcastMessageWithFallback(
  thread: Thread<BotThreadState>,
  text: string
) {
  try {
    await postBroadcastMessage(thread, text);
  } catch {
    await thread.post(text);
  }
}

async function fetchProtocolInput(url: string) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch URL: ${res.status}`);
  }

  const buffer = Buffer.from(await res.arrayBuffer());
  const contentType = res.headers.get("content-type") || "";
  const urlPath = new URL(url).pathname;
  const fileName = urlPath.split("/").pop() || "document";

  return {
    fileData: buffer,
    fileName: contentType.includes("text") ? undefined : fileName,
    text: contentType.includes("text")
      ? new TextDecoder().decode(buffer)
      : undefined,
  };
}

async function uploadMarkdownFile(
  thread: Thread<BotThreadState>,
  filename: string,
  markdown: string
) {
  const buffer = Buffer.from(markdown, "utf-8");
  await fs.writeFile(`/tmp/${filename}`, buffer);

  await thread.post({
    raw: "",
    files: [
      {
        data: buffer,
        filename,
        mimeType: "text/markdown",
      },
    ],
  });
}

async function postAnswer(thread: Thread<BotThreadState>, answer: string) {
  const chunks = splitMarkdown(answer);
  for (const chunk of chunks) {
    await thread.post({ markdown: chunk });
  }
}

async function runParseForThread(
  thread: Thread<BotThreadState>,
  modelId: string,
  state: BotThreadState
) {
  if (!state.sourceUrl) {
    await thread.post("No protocol URL found for this thread.");
    return;
  }

  await postBroadcastMessageWithFallback(
    thread,
    `Parsing protocol with \`${modelId}\`... this may take a couple minutes.`
  );

  try {
    const input = await fetchProtocolInput(state.sourceUrl);
    const markdown = await parseProtocolMarkdown(state.sourceUrl, input, modelId);
    const title = extractTitle(markdown);
    const slug = slugify(title);

    await uploadMarkdownFile(thread, `${slug}-parsed.md`, markdown);
    await postBroadcastMessageWithFallback(
      thread,
      `Here's the parsed protocol for *${title}*. Reply with questions or feedback.`
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Parse failed";
    await thread.post(`Error: ${msg}`);
  }
}

async function reviseProtocol(
  thread: Thread<BotThreadState>,
  state: BotThreadState,
  feedback: string
) {
  if (!state.parsedMarkdown) {
    await thread.post("No parsed protocol in this thread.");
    return;
  }

  const modelId = state.selectedModel || DEFAULT_MODEL;

  await postBroadcastMessageWithFallback(
    thread,
    `Revising the parsed protocol with \`${modelId}\`, may take a couple mins...`
  );

  try {
    const { text: revisedMarkdown } = await generateText({
      model: resolveModel(modelId),
      system:
        "You are cDNA, a sequencing protocol expert. Revise the parsed protocol markdown " +
        "based on the user's feedback. Return only the complete revised markdown document. " +
        "Preserve markdown structure.\n\n" +
        `Source URL: ${state.sourceUrl || "unknown"}\n\n` +
        `Current parsed protocol:\n\n${state.parsedMarkdown}`,
      prompt: `User feedback to apply:\n${feedback}`,
    });

    const title = extractTitle(revisedMarkdown);
    const slug = slugify(title);

    await uploadMarkdownFile(thread, `${slug}-parsed.md`, revisedMarkdown);
    await postBroadcastMessageWithFallback(
      thread,
      `I've revised the parsed protocol for *${title}*. The updated markdown is in the attached file.`
    );
    await postBroadcastMessageWithFallback(
      thread,
      "Please review it. Reply with questions or more feedback."
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Revision failed";
    await thread.post(`Error: ${msg}`);
  }
}

async function handleThreadReply(
  thread: Thread<BotThreadState>,
  messageText: string
): Promise<boolean> {
  const url = extractUrl(messageText);
  if (url) {
    await runParseForThread(thread, PARSE_MODEL, { sourceUrl: url });
    return true;
  }

  const state = await recoverThreadContext(thread);
  if (!state) {
    return false; // Bot hasn't participated in this thread — ignore
  }

  if (state.status === "parsing") {
    await thread.post(
      "Still parsing this protocol. I'll post the file here when it's ready."
    );
    return true;
  }

  if (!state.parsedMarkdown) {
    await thread.post("No parsed protocol in this thread.");
    return true;
  }

  if (isRevisionRequest(messageText)) {
    await reviseProtocol(thread, state, messageText);
    return true;
  }

  try {
    const { text: answer } = await generateText({
      model: resolveModel(state.selectedModel || DEFAULT_MODEL),
      system:
        "You are cDNA, a sequencing protocol expert. A user has just parsed " +
        "a protocol and is asking follow-up questions. Here is the parsed protocol:\n\n" +
        state.parsedMarkdown,
      prompt: messageText,
    });

    await postAnswer(thread, answer);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to answer";
    await thread.post(`Error: ${msg}`);
  }
  return true;
}

function registerHandlers(bot: Chat<{ slack: SlackAdapter }, BotThreadState>) {
  bot.onNewMention(async (thread, message) => {
    if (message.author.isMe || message.author.isBot === true) {
      return;
    }

    const url = extractUrl(message.text);
    if (url) {
      await runParseForThread(thread as Thread<BotThreadState>, PARSE_MODEL, {
        sourceUrl: url,
      });
      return;
    }

    const handled = await handleThreadReply(
      thread as Thread<BotThreadState>,
      message.text
    );
    if (!handled) {
      await thread.post(
        "Send me a protocol URL and I'll parse it for you."
      );
    }
  });

  // Use onNewMessage instead of onSubscribedMessage so replies work
  // even after serverless cold starts (no subscription persistence needed).
  bot.onNewMessage(/./, async (thread, message) => {
    if (message.author.isMe || message.author.isBot === true) {
      return;
    }

    await handleThreadReply(
      thread as Thread<BotThreadState>,
      message.text
    );
  });
}

export { getBot };
