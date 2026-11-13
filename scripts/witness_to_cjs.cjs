const fs = require("fs");

const dir = "circuits/build/hashlogin_js";
for (const f of ["generate_witness", "witness_calculator"]) {
  fs.renameSync(`${dir}/${f}.js`, `${dir}/${f}.cjs`);
}
const gen = `${dir}/generate_witness.cjs`;
fs.writeFileSync(
  gen,
  fs.readFileSync(gen, "utf8").replace("./witness_calculator.js", "./witness_calculator.cjs")
);
console.log("witness generator renamed to .cjs");
