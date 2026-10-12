export function contentDisposition(inline: boolean, filename: string): string {
  const kind = inline ? "inline" : "attachment";
  const ascii = asciiFallback(filename);
  return `${kind}; filename="${ascii}"; filename*=UTF-8''${encodeRfc5987(filename)}`;
}

function asciiFallback(filename: string): string {
  const cleaned = filename.replace(/[^\x20-\x7E]/g, "_").replace(/["\\]/g, "_");
  return cleaned.trim() || "download";
}

function encodeRfc5987(value: string): string {
  return encodeURIComponent(value).replace(
    /['()*]/g,
    (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}
