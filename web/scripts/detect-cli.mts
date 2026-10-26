// Reads [{text}] as JSON on stdin and prints [{tokens, instruction}] for the Python parity test.
// node --experimental-strip-types web/scripts/detect-cli.mts < cases.json
import { looksLikeInstruction, tokensIn } from "../lib/detect.ts";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const cases: { text: string }[] = JSON.parse(input);
  const out = cases.map((c) => ({ tokens: [...tokensIn(c.text)].sort(), instruction: looksLikeInstruction(c.text) }));
  process.stdout.write(JSON.stringify(out));
});
