import assert from "node:assert/strict";
import { test } from "node:test";

import type { EvidenceRecord } from "../src/lib/analysis";
import {
  interpretMediaResponse,
  localImageName,
  mediaCaption,
  mediaUrl,
  remoteImageAttachments,
} from "../src/lib/source-media";

const record = (content: unknown, source_file = "Users/Test/Chrome/History"): EvidenceRecord => ({
  record_id: "r 1", case_id: "case/a", source_file, source_locator: "x",
  content: typeof content === "string" ? content : JSON.stringify(content), timestamp: null,
});

test("an image download or an image source file names its local image", () => {
  assert.equal(localImageName(record({ target_path: "C:\\Users\\T\\Pictures\\a b.PNG" })), "a b.PNG");
  assert.equal(localImageName(record({ target_path: "C:\\Users\\T\\Downloads\\setup.exe" })), null);
  assert.equal(localImageName(record("plain text", "Users/T/scan.jpeg")), "scan.jpeg");
  assert.equal(localImageName(record("{broken")), null);
});

test("remote attachments are listed by name and host, never as loadable URLs", () => {
  const attachments = remoteImageAttachments(record({ attachments: [
    { filename: "Steve_K.PNG", url: "https://cdn.discordapp.com/attachments/1/2/Steve_K.PNG" },
    { filename: "notes.txt", url: "https://cdn.discordapp.com/x" },
    { filename: "x.jpg", url: "not a url" },
  ] }));
  assert.deepEqual(attachments, [{ filename: "Steve_K.PNG", host: "cdn.discordapp.com" }, { filename: "x.jpg", host: "a remote service" }]);
  assert.ok(!JSON.stringify(attachments).includes("https://"));
  assert.deepEqual(remoteImageAttachments(record({ attachments: "nope" })), []);
});

test("the media URL is case-scoped and encoded", () => {
  assert.match(mediaUrl("case/a", "r 1"), /\/cases\/case%2Fa\/records\/r%201\/media$/);
});

test("SourceImage load states: 404 missing, 409 integrity, 200 caption by hash", () => {
  const headers = (verified: string | null) => ({
    get: (name: string) => (name === "X-Hash-Verified" ? verified : null),
  });
  assert.deepEqual(interpretMediaResponse(404, headers(null)), { status: "missing" });
  assert.deepEqual(interpretMediaResponse(409, headers(null)), { status: "integrity" });
  assert.deepEqual(interpretMediaResponse(500, headers(null)), { status: "missing" });
  assert.deepEqual(interpretMediaResponse(200, headers("true")), {
    status: "ready",
    hashVerified: true,
  });
  assert.deepEqual(interpretMediaResponse(200, headers("false")), {
    status: "ready",
    hashVerified: false,
  });
  assert.deepEqual(interpretMediaResponse(200, headers(null)), {
    status: "ready",
    hashVerified: false,
  });
  assert.match(mediaCaption("a.png", true), /original file from local evidence/);
  assert.match(mediaCaption("a.png", false), /hash not checked/);
  assert.ok(!mediaCaption("a.png", false).includes("original file from local evidence"));
});
