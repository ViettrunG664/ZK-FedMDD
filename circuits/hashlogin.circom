pragma circom 2.0.0;

// Resolved through `-l ./circomlib/circuits` (see the build:circuits script).
include "poseidon.circom";

// Proves knowledge of `secret` with Poseidon(secret) == hashOut, bound to one
// context (taskId, round, payload) so a proof cannot be replayed elsewhere.
// Public signals, in order: [hashOut, taskId, round, payload].
//   payload = uint256(weightsHash) mod p for submitUpdate, 0 where unused.
//   (taskId, round, payload) = (0, 0, 0) is the login context.
template Main() {
  signal input  secret;
  signal input  taskId;
  signal input  round;
  signal input  payload;
  signal output hashOut;

  component p = Poseidon(1);
  p.inputs[0] <== secret;
  hashOut <== p.out;

  // Tie the context signals into the constraint system.
  signal taskSq;
  signal roundSq;
  signal payloadSq;
  taskSq    <== taskId * taskId;
  roundSq   <== round * round;
  payloadSq <== payload * payload;
}

component main {public [taskId, round, payload]} = Main();
