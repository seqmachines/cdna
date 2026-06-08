"use client";

import { FormEvent, ReactNode, useMemo, useRef, useState } from "react";

type ScgReportMetadata = {
  protocol_name: string | null;
  published_date: string | null;
  company: string | null;
  document_reference: string | null;
  source_url: string | null;
  brief_description: string | null;
};

type ScgReportResult = {
  mode: "scg_report";
  metadata: ScgReportMetadata;
  sections: {
    metadata: string;
    adapter_primer_sequences: string;
    library_generation: string;
    library_sequencing: string;
  };
  report_markdown: string;
  warnings: string[];
};

type MarkdownBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "code"; text: string };

const DEFAULT_MODEL = "google/gemini-3.1-pro-preview";

function parseMarkdown(markdown: string): MarkdownBlock[] {
  const lines = markdown.split(/\r?\n/);
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let fencedCode: string[] | null = null;
  let indentedCode: string[] = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    blocks.push({ kind: "paragraph", text: paragraph.join(" ").trim() });
    paragraph = [];
  }

  function flushList() {
    if (!list.length) return;
    blocks.push({ kind: "list", items: list });
    list = [];
  }

  function flushIndentedCode() {
    if (!indentedCode.length) return;
    blocks.push({ kind: "code", text: indentedCode.join("\n") });
    indentedCode = [];
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushParagraph();
      flushList();
      flushIndentedCode();
      if (fencedCode) {
        blocks.push({ kind: "code", text: fencedCode.join("\n") });
        fencedCode = null;
      } else {
        fencedCode = [];
      }
      continue;
    }

    if (fencedCode) {
      fencedCode.push(line);
      continue;
    }

    const indented = line.match(/^(?: {4}|\t)(.*)$/);
    if (indented) {
      flushParagraph();
      flushList();
      indentedCode.push(indented[1]);
      continue;
    }

    flushIndentedCode();

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        text: heading[2].trim(),
      });
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1].trim());
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  if (fencedCode) blocks.push({ kind: "code", text: fencedCode.join("\n") });
  flushIndentedCode();
  flushParagraph();
  flushList();
  return blocks;
}

function InlineMarkdown({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
}

function MarkdownView({ markdown }: { markdown: string }) {
  const blocks = useMemo(() => parseMarkdown(markdown), [markdown]);

  return (
    <div className="markdown-view">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          const Tag = block.level <= 2 ? "h2" : block.level === 3 ? "h3" : "h4";
          return (
            <Tag key={index}>
              <InlineMarkdown text={block.text} />
            </Tag>
          );
        }

        if (block.kind === "list") {
          return (
            <ul key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>
                  <InlineMarkdown text={item} />
                </li>
              ))}
            </ul>
          );
        }

        if (block.kind === "code") {
          return <pre key={index}>{block.text}</pre>;
        }

        return (
          <p key={index}>
            <InlineMarkdown text={block.text} />
          </p>
        );
      })}
    </div>
  );
}

function MetadataItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metadata-item">
      <dt>{label}</dt>
      <dd>{value || <span className="muted">Not found</span>}</dd>
    </div>
  );
}

function Section({
  id,
  title,
  markdown,
}: {
  id: string;
  title: string;
  markdown: string;
}) {
  return (
    <section className="report-section" id={id}>
      <div className="section-heading">
        <p>{id.replaceAll("-", " ")}</p>
        <h2>{title}</h2>
      </div>
      {markdown ? <MarkdownView markdown={markdown} /> : <p className="muted">Not generated.</p>}
    </section>
  );
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [result, setResult] = useState<ScgReportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Choose a protocol PDF first.");
      return;
    }

    setIsLoading(true);
    setError(null);
    setResult(null);

    const formData = new FormData();
    formData.append("mode", "scg_report");
    formData.append("file", file);
    if (model.trim()) formData.append("model", model.trim());

    try {
      const response = await fetch("/api/one-pass-baseline", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Protocol parsing failed.");
      }

      setResult(data as ScgReportResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Protocol parsing failed.");
    } finally {
      setIsLoading(false);
    }
  }

  const protocolName = result?.metadata.protocol_name || "Protocol report";

  return (
    <main className="app-shell">
      <aside className="control-panel">
        <div>
          <p className="eyebrow">cDNA demo</p>
          <h1>Protocol library parser</h1>
        </div>

        <form onSubmit={submit} className="upload-form">
          <button
            type="button"
            className="drop-zone"
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              const droppedFile = event.dataTransfer.files.item(0);
              if (droppedFile) setFile(droppedFile);
            }}
          >
            <span>{file ? file.name : "Drop protocol PDF"}</span>
            <small>{file ? `${Math.ceil(file.size / 1024)} KB` : "PDF upload"}</small>
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf,text/plain,.txt,.md"
            onChange={(event) => setFile(event.target.files?.item(0) || null)}
            hidden
          />

          <label className="field-label">
            Model
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              spellCheck={false}
            />
          </label>

          <button className="primary-button" type="submit" disabled={isLoading}>
            {isLoading ? "Parsing..." : "Parse protocol"}
          </button>
        </form>

        {error ? <div className="status error">{error}</div> : null}
        {result?.warnings.length ? (
          <div className="status warning">
            {result.warnings.slice(0, 3).map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        ) : null}

        <nav className="section-nav" aria-label="Report sections">
          <a href="#adapter-primer">Adapters</a>
          <a href="#library-generation">Generation</a>
          <a href="#library-sequencing">Sequencing</a>
        </nav>
      </aside>

      <section className="report-panel">
        <header className="report-header">
          <div>
            <p className="eyebrow">scg_lib_structs style report</p>
            <h2>{protocolName}</h2>
            {result?.metadata.brief_description ? (
              <p>{result.metadata.brief_description}</p>
            ) : null}
          </div>
        </header>

        {result ? (
          <>
            <dl className="metadata-grid">
              <MetadataItem label="Published" value={result.metadata.published_date} />
              <MetadataItem label="Company" value={result.metadata.company} />
              <MetadataItem label="Reference" value={result.metadata.document_reference} />
              <MetadataItem label="Source" value={result.metadata.source_url} />
            </dl>

            <Section
              id="adapter-primer"
              title="Adapter And Primer Sequences"
              markdown={result.sections.adapter_primer_sequences}
            />
            <Section
              id="library-generation"
              title="Step-By-Step Library Generation"
              markdown={result.sections.library_generation}
            />
            <Section
              id="library-sequencing"
              title="Library Sequencing"
              markdown={result.sections.library_sequencing}
            />
          </>
        ) : (
          <div className="empty-state">
            <h2>Ready for a protocol PDF</h2>
            <p>The parsed library structure will appear here.</p>
          </div>
        )}
      </section>
    </main>
  );
}
