import assert from "node:assert/strict";
import test from "node:test";
import { openCaptionWindow, prepareCaptionWindow } from "../src/caption-window.ts";
import { readFileSync } from "node:fs";

test("detached window receives complete inline styling without fetching stylesheet links", () => {
  const element = (tag) => ({ tag, children: [], append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; } });
  const doc = { head: element("head"), body: element("body"), documentElement: {}, createElement: element };
  const css = readFileSync(new URL("../src/studio.css", import.meta.url), "utf8");
  const root = prepareCaptionWindow({ document: doc }, {
    baseURI: "http://127.0.0.1:8765/", documentElement: { lang: "zh-CN" },
  }, css);
  const style = doc.head.children.find((node) => node.tag === "style");
  assert.equal(style.textContent, css);
  assert.match(style.textContent, /\.live-caption-current/);
  assert.match(style.textContent, /body\.caption-window/);
  assert.ok(!doc.head.children.some((node) => node.tag === "link"));
  assert.equal(doc.body.className, "caption-window");
  assert.equal(doc.body.children[0], root);
});

test("uses native always-on-top window when available", async () => {
  const target = {};
  const result = await openCaptionWindow({
    documentPictureInPicture: {
      requestWindow: async (options) => {
        assert.ok(options.width > 0 && options.height > 0);
        return target;
      },
    },
    open: () => assert.fail("must not open a second window"),
  });
  assert.equal(result.window, target);
  assert.equal(result.kind, "pip");
});

test("unsupported browsers open a popup synchronously in the click handler", async () => {
  const target = {};
  let opened = false;
  const promise = openCaptionWindow({ open: (_, name, features) => {
    opened = true;
    assert.equal(name, "_blank");
    assert.match(features, /popup/);
    return target;
  } });
  assert.ok(opened, "do not await before requesting the popup");
  const result = await promise;
  assert.equal(result.window, target);
  assert.equal(result.kind, "popup");
});

test("disabled picture-in-picture falls back without losing the display", async () => {
  const target = {};
  const result = await openCaptionWindow({
    documentPictureInPicture: { requestWindow: async () => { throw new Error("disabled"); } },
    open: () => target,
  });
  assert.equal(result.window, target);
  assert.equal(result.kind, "popup");
});

test("blocked popup reports an error instead of leaving a blank viewer", async () => {
  await assert.rejects(openCaptionWindow({ open: () => null }), /允许.*弹窗/);
});

test("popup must never reuse the main page or an already closed window", async () => {
  const host = { open: () => host };
  await assert.rejects(openCaptionWindow(host), /没有提供独立窗口/);
  await assert.rejects(openCaptionWindow({ open: () => ({ closed: true }) }), /没有提供独立窗口/);
});

test("unresponsive picture-in-picture falls back and closes a late orphan window", async () => {
  let resolve;
  let lateClosed = false;
  const popup = {};
  const result = await openCaptionWindow({
    documentPictureInPicture: { requestWindow: () => new Promise((done) => { resolve = done; }) },
    open: () => popup,
  });
  assert.equal(result.window, popup);
  assert.equal(result.kind, "popup");
  resolve({ close: () => { lateClosed = true; } });
  await Promise.resolve();
  assert.ok(lateClosed);
});
