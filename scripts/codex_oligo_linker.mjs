import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const sdkPath = payload.sdk_path;
  const prompt = payload.prompt || "";
  const codexModel = payload.model || "";
  const reasoningEffort = payload.reasoning_effort || "";
  const workingDirectory = payload.working_directory || "";
  const sandboxMode = payload.sandbox_mode || "read-only";
  const skipGitRepoCheck = payload.skip_git_repo_check === true;
  const additionalDirectories = Array.isArray(payload.additional_directories)
    ? payload.additional_directories.filter((value) => typeof value === "string" && value.length > 0)
    : [];

  if (!sdkPath) {
    throw new Error("Missing sdk_path");
  }
  await readFile(sdkPath, "utf8");
  const { Codex } = await import(pathToFileURL(sdkPath).href);
  const codex = new Codex({
    apiKey: process.env.CODEX_API_KEY ?? process.env.OPENAI_API_KEY,
  });
  const threadOptions = {
    sandboxMode,
    skipGitRepoCheck,
    approvalPolicy: "never",
    webSearchMode: "disabled",
    networkAccessEnabled: false,
  };
  if (workingDirectory) {
    threadOptions.workingDirectory = workingDirectory;
  }
  if (additionalDirectories.length > 0) {
    threadOptions.additionalDirectories = additionalDirectories;
  }
  if (codexModel) {
    threadOptions.model = codexModel;
  }
  if (reasoningEffort) {
    threadOptions.modelReasoningEffort = reasoningEffort;
  }
  const thread = codex.startThread(threadOptions);
  const turn = await thread.run(prompt);
  process.stdout.write(JSON.stringify({ text: turn.finalResponse ?? "" }) + "\n");
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(message + "\n");
  process.exit(1);
});
