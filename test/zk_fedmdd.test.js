import crypto from "crypto";
import { expect } from "chai";
import pkg from "hardhat";
import { proveFacility, toField, SNARK_FIELD } from "../utils/zkproof.js";
const { ethers } = pkg;

const randSecret = () => BigInt("0x" + crypto.randomBytes(32).toString("hex")) % SNARK_FIELD;
const randHash = () => "0x" + crypto.randomBytes(32).toString("hex");
const CID = "bafkrei" + "a".repeat(52);

describe("ZK-FedMDD facility authentication", function () {
  this.timeout(120000);

  let admin, outsider, ia, fl, facilities;

  before(async () => {
    [admin, outsider] = await ethers.getSigners();
    const verifier = await (await ethers.getContractFactory("Groth16Verifier")).deploy();
    ia = await (await ethers.getContractFactory("IdentityAccess")).deploy(await verifier.getAddress());
    fl = await (await ethers.getContractFactory("FederatedLearning"))
      .deploy(await ia.getAddress(), await verifier.getAddress());

    facilities = [];
    for (let i = 0; i < 3; i++) {
      const secret = randSecret();
      const { hashId } = await proveFacility(secret);
      await ia.register(hashId, ethers.ZeroHash, true, admin.address);
      facilities.push({ secret, hashId });
    }
  });

  async function createTask(creator, rounds = 3) {
    const initialWeightsHash = randHash();
    const taskId = (await fl.taskCount()) + 1n;
    const { proof } = await proveFacility(creator.secret, { taskId, payload: toField(initialWeightsHash) });
    await fl.createTask(
      { modelHash: randHash(), description: "t", totalRounds: rounds, minParticipants: 2,
        initialModelCID: CID, initialWeightsHash },
      proof, creator.hashId
    );
    return taskId;
  }

  async function join(taskId, f) {
    const { proof } = await proveFacility(f.secret, { taskId });
    await fl.joinTask(taskId, proof, f.hashId);
  }

  it("stores no witness-derived value on-chain", async () => {
    const u = await ia.users(facilities[0].hashId);
    expect(u.length).to.equal(3);
    expect(u[2]).to.equal(facilities[0].hashId);
  });

  it("only the registrar can register identities", async () => {
    const { hashId } = await proveFacility(randSecret());
    await expect(ia.connect(outsider).register(hashId, ethers.ZeroHash, true, outsider.address))
      .to.be.revertedWith("only-admin");
  });

  it("accepts a proof bound to the right task, round and weights", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    await join(taskId, f1);
    await fl.startRound(taskId);
    const wh = randHash();
    const { proof } = await proveFacility(f1.secret, { taskId, round: 1n, payload: toField(wh) });
    await expect(fl.submitUpdate(taskId, wh, CID, 6900, 150, proof, f1.hashId))
      .to.emit(fl, "UpdateSubmitted");
  });

  it("rejects a round-1 proof replayed in round 2", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    await join(taskId, f1);
    await fl.startRound(taskId);
    const round1 = [];
    for (const f of [f0, f1]) {
      const wh = randHash();
      const { proof } = await proveFacility(f.secret, { taskId, round: 1n, payload: toField(wh) });
      await fl.submitUpdate(taskId, wh, CID, 6900, 150, proof, f.hashId);
      round1.push({ wh, proof, f });
    }
    await fl.submitAggregation(taskId, CID, randHash());
    await fl.startRound(taskId);
    const { wh, proof, f } = round1[0];
    await expect(fl.submitUpdate(taskId, wh, CID, 6900, 150, proof, f.hashId))
      .to.be.revertedWith("FL: invalid ZK proof");
  });

  it("rejects a second submission by the same facility in the same round", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    await join(taskId, f1);
    await fl.startRound(taskId);
    for (const expectRevert of [false, true]) {
      const wh = randHash();
      const { proof } = await proveFacility(f1.secret, { taskId, round: 1n, payload: toField(wh) });
      const tx = fl.submitUpdate(taskId, wh, CID, 6900, 150, proof, f1.hashId);
      if (expectRevert) await expect(tx).to.be.revertedWith("FL: already submitted this round");
      else await tx;
    }
  });

  it("rejects a proof with one group element perturbed", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    await join(taskId, f1);
    await fl.startRound(taskId);
    const wh = randHash();
    const { proof } = await proveFacility(f1.secret, { taskId, round: 1n, payload: toField(wh) });
    const bad = { ...proof, a: [(BigInt(proof.a[0]) + 1n).toString(), proof.a[1]] };
    await expect(fl.submitUpdate(taskId, wh, CID, 6900, 150, bad, f1.hashId))
      .to.be.revertedWith("FL: invalid ZK proof");
  });

  it("rejects a proof whose weights hash was swapped", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    await join(taskId, f1);
    await fl.startRound(taskId);
    const { proof } = await proveFacility(f1.secret, { taskId, round: 1n, payload: toField(randHash()) });
    await expect(fl.submitUpdate(taskId, randHash(), CID, 6900, 150, proof, f1.hashId))
      .to.be.revertedWith("FL: invalid ZK proof");
  });

  it("rejects a join proof reused for another task", async () => {
    const [f0, , f2] = facilities;
    const t1 = await createTask(f0);
    const t2 = await createTask(f0);
    const { proof } = await proveFacility(f2.secret, { taskId: t1 });
    await expect(fl.joinTask(t2, proof, f2.hashId)).to.be.revertedWith("FL: invalid ZK proof");
  });

  it("rejects a valid proof sent by a wallet that does not sponsor the facility", async () => {
    const [f0, f1] = facilities;
    const taskId = await createTask(f0);
    const { proof } = await proveFacility(f1.secret, { taskId });
    await expect(fl.connect(outsider).joinTask(taskId, proof, f1.hashId))
      .to.be.revertedWith("FL: facility not owned by caller");
  });

  it("zkLogin accepts only the login context (0, 0, 0)", async () => {
    const f = facilities[0];
    const login = await proveFacility(f.secret);
    expect(await ia.zkLogin(login.proof.a, login.proof.b, login.proof.c, f.hashId)).to.equal(true);
    const other = await proveFacility(f.secret, { taskId: 1n });
    expect(await ia.zkLogin(other.proof.a, other.proof.b, other.proof.c, f.hashId)).to.equal(false);
  });
});
