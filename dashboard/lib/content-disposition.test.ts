import assert from "node:assert/strict";
import test from "node:test";
import { contentDisposition } from "./content-disposition.ts";

test("encodes a non-Latin-1 filename as an ASCII header", () => {
  const header = contentDisposition(true, "स्टेटमेंट.pdf");
  assert.match(header, /^inline; filename="_+\.pdf"; filename\*=UTF-8''/);
  assert.match(header, /filename\*=UTF-8''%E0%A4%B8/);
  assert.equal([...header].every((char) => char.charCodeAt(0) <= 0xff), true);
  assert.doesNotThrow(() => new Headers({ "Content-Disposition": header }));
});

test("keeps an ASCII name and escapes quotes", () => {
  const header = contentDisposition(false, 'statement "oct".pdf');
  assert.match(header, /^attachment; filename="statement _oct_\.pdf"; filename\*=UTF-8''/);
  assert.match(header, /filename\*=UTF-8''statement%20%22oct%22\.pdf$/);
  assert.doesNotThrow(() => new Headers({ "Content-Disposition": header }));
});
