const TYPES: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  pdf: "application/pdf",
  csv: "text/csv; charset=utf-8",
  txt: "text/plain; charset=utf-8",
  json: "application/json; charset=utf-8",
};

export function contentTypeFor(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
  return TYPES[ext] ?? "application/octet-stream";
}

export function isInline(filePath: string): boolean {
  const type = contentTypeFor(filePath);
  return type.startsWith("image/") || type === "application/pdf";
}

export function isImagePath(filePath: string): boolean {
  return contentTypeFor(filePath).startsWith("image/");
}
