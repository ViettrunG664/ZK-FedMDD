import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";

export default buildModule("IdentityModule", (m) => {
  // 1) Verifier
  const verifier = m.contract("Verifier");
  // 2) IdentityAccess với địa chỉ verifier
  const identity = m.contract("IdentityAccess", [verifier]);
  return { verifier, identity };
});
