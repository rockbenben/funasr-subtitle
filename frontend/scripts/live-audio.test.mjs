import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function processor() {
  let Capture;
  const messages = [];
  vm.runInNewContext(readFileSync(new URL("../public/pcm-capture.js", import.meta.url), "utf8"), {
    Int16Array,
    AudioWorkletProcessor: class {
      port = { postMessage: (message) => messages.push(message) };
    },
    registerProcessor: (_, implementation) => { Capture = implementation; },
  });
  return { capture: new Capture(), messages };
}

test("600ms packets and final tail preserve every sample exactly once", () => {
  const { capture, messages } = processor();
  const samples = new Float32Array(128).fill(0.5);
  for (let i = 0; i < 80; i++) capture.process([[samples]]);
  capture.port.onmessage({ data: "stop" });
  assert.equal(messages.length, 3);
  assert.equal(messages[0].byteLength, 19200);
  assert.equal(messages[1].byteLength, (128 * 80 - 9600) * 2);
  assert.equal(messages[2], "stopped");
  const pcm = [...new Int16Array(messages[0]), ...new Int16Array(messages[1])];
  assert.equal(pcm.length, 128 * 80);
  assert.ok(pcm.every((sample) => sample === 16384));
  assert.equal(capture.process([[samples]]), false);
});

test("stereo is mixed to mono and out-of-range samples are clipped", () => {
  const { capture, messages } = processor();
  capture.process([[new Float32Array([0.5, 2, -2]), new Float32Array([-0.5, 2, -2])]]);
  capture.port.onmessage({ data: "stop" });
  assert.deepEqual([...new Int16Array(messages[0])], [0, 32767, -32768]);
});

test("stopping before the first audio frame sends no bogus PCM", () => {
  const { capture, messages } = processor();
  capture.port.onmessage({ data: "stop" });
  assert.deepEqual(messages, ["stopped"]);
});
