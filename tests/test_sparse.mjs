// JS half of the cross-language sparse-tokenizer parity test -- FIXTURE is
// duplicated verbatim from tests/test_sparse.py. Run: node tests/test_sparse.mjs
import assert from "node:assert/strict";
import { sparseVector, tokenize } from "../api/qdrantStore.js";

const FIXTURE = {
  "one piece one": [[3123124719, 4106613381], [2, 1]],
  "One Piece: Pirate King ワンピース K‑ON!": [
    [287202141, 435066257, 3026372832, 3123124719, 3658736598, 4106613381],
    [1, 1, 1, 1, 1, 1],
  ],
  "a I x": [[], []],
  "": [[], []],
};

assert.deepEqual(tokenize("One Piece: Pirate King"), ["one", "piece", "pirate", "king"]);
assert.deepEqual(tokenize("K‑ON!"), ["k‐on"]);
for (const [text, [indices, values]] of Object.entries(FIXTURE)) {
  const sv = sparseVector(text);
  assert.deepEqual(sv.indices, indices, text);
  assert.deepEqual(sv.values, values, text);
}
console.log("test_sparse.mjs: all assertions passed");
