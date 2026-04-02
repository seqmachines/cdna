declare module "pdf-parse" {
  interface PDFData {
    numpages: number;
    text: string;
    info: Record<string, unknown>;
  }
  export default function pdfParse(buffer: Buffer): Promise<PDFData>;
}
