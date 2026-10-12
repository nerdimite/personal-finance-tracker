import { contentDisposition } from "@/lib/content-disposition";
import { contentTypeFor, isInline } from "@/lib/content-type";
import { messagesDir } from "@/lib/env";
import { resolveInside } from "@/lib/safe-path";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  _request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path: segments } = await context.params;
  const file = resolveInside(messagesDir(), segments ?? []);
  if (!file) return notFound();

  const filename = path.basename(file);
  const disposition = contentDisposition(isInline(file), filename);
  const contentType = contentTypeFor(file);

  let fd: number;
  try {
    fd = fs.openSync(file, "r");
  } catch {
    return notFound();
  }

  let stat: fs.Stats;
  try {
    stat = fs.fstatSync(fd);
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }

  let headers: Headers;
  try {
    headers = new Headers({
      "Content-Type": contentType,
      "Content-Length": String(stat.size),
      "Content-Disposition": disposition,
      "X-Content-Type-Options": "nosniff",
      "Cache-Control": "private, no-store",
    });
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }

  const stream = fs.createReadStream(file, { fd, autoClose: true });
  return new Response(Readable.toWeb(stream) as ReadableStream, { status: 200, headers });
}

function notFound(): Response {
  return new Response("Not found", {
    status: 404,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" },
  });
}
