import * as snarkjs from "snarkjs";

export const SNARK_FIELD =
  21888242871839275222246405745257275088548364400416034343698204186575808495617n;

const WASM = "circuits/build/hashlogin_js/hashlogin.wasm";
const ZKEY = "circuits/build/hashlogin_final.zkey";

const toBytes32 = (x) => "0x" + BigInt(x).toString(16).padStart(64, "0");

export const toField = (bytes32) => BigInt(bytes32) % SNARK_FIELD;

export async function proveFacility(secret, { taskId = 0n, round = 0n, payload = 0n } = {}) {
  const { proof, publicSignals } = await snarkjs.groth16.fullProve(
    {
      secret: secret.toString(),
      taskId: taskId.toString(),
      round: round.toString(),
      payload: payload.toString(),
    },
    WASM,
    ZKEY
  );
  return {
    proof: {
      a: [proof.pi_a[0], proof.pi_a[1]],
      b: [
        [proof.pi_b[0][1], proof.pi_b[0][0]],
        [proof.pi_b[1][1], proof.pi_b[1][0]],
      ],
      c: [proof.pi_c[0], proof.pi_c[1]],
    },
    hashId: toBytes32(publicSignals[0]),
    publicSignals,
  };
}
