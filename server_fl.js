// FL Server - Port 3002
// Bridges Frontend <-> FL Service (Python) <-> Blockchain <-> Filecoin

import 'dotenv/config';

import cors from 'cors';
import express from 'express';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import pkg from 'hardhat';
const { ethers } = pkg;
import { pushToFilecoin } from './utils/filecoin.js';
import { encryptAES } from './utils/encryption.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = process.env.FL_PORT || 3002;
const FL_SERVICE_URL = 'http://localhost:7001';
const ZK_PROVE_URL = 'http://localhost:7000';
const app = express();

app.use(express.json({ limit: '50mb' }));
app.use(cors({
  origin: ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:3002'],
  methods: ['POST', 'GET'],
  allowedHeaders: ['Content-Type'],
}));

app.use(express.static(path.join(__dirname, 'FE')));

// Request timeout
app.use((req, res, next) => {
  req.setTimeout(300000); // 5 min
  res.setTimeout(300000);
  next();
});

/* ─── Contract Helper ─── */
async function getFlContract() {
  const addr = fs.readFileSync('fl_contract_address.txt', 'utf8').trim();
  return ethers.getContractAt('FederatedLearning', addr);
}

async function getSigner() {
  const [signer] = await ethers.getSigners();
  return signer;
}

/* ─── FL Service Helper ─── */
async function flFetch(endpoint, options = {}) {
  const url = `${FL_SERVICE_URL}${endpoint}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`FL Service error (${res.status}): ${text}`);
  }
  return res.json();
}

/**
 * Fetch the raw npz-serialized weights for a key from the FL service.
 * @param {string} key - weights_key returned by /model/init, /train, /aggregate
 * @returns {Promise<{key:string, sha256:string, bytes:number, b64:string}>}
 */
async function fetchWeightsBlob(key) {
  const res = await fetch(`${FL_SERVICE_URL}/weights/${encodeURIComponent(key)}/blob`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`FL Service blob error (${res.status}): ${text}`);
  }
  return res.json();
}

/**
 * Build an AES-256-encrypted Filecoin payload for a set of model weights.
 * The weight bytes and any sensitive metadata are encrypted together; only the
 * integrity hash (SHA-256 of the plaintext npz, i.e. the value committed
 * on-chain) is left in the clear so the ciphertext can be matched to its
 * on-chain record. Retrieval reverses this with decryptAES using the same
 * AES_KEY. This closes the content-level confidentiality gap for weights at
 * rest on IPFS/Filecoin (mitigating model/gradient-inversion exposure).
 * @param {string} weightsKey - key to fetch from the FL service
 * @param {string} type - payload type tag (e.g. 'fl_local_weights')
 * @param {string} sha256 - plaintext integrity hash, as committed on-chain
 * @param {object} secretMeta - extra fields to encrypt alongside the weights
 * @returns {Promise<object>} encrypted payload ready for pushToFilecoin
 */
async function buildEncryptedWeightsPayload(weightsKey, type, sha256, secretMeta = {}) {
  const blob = await fetchWeightsBlob(weightsKey);
  const plaintext = JSON.stringify({ weights_b64: blob.b64, sha256, ...secretMeta });
  const { encrypted, iv } = encryptAES(plaintext);
  return {
    type,
    alg: 'aes-256-cbc',
    iv,
    ciphertext: encrypted,
    sha256,          // integrity hash over PLAINTEXT weights (matches on-chain)
    bytes: blob.bytes,
  };
}

/* ─── ZK Proof Helper ─── */
/**
 * Generate ZK proof + hashId for a facility, using hospital-bound practitioner
 * identity (HOSPITAL:DOCTOR_ID) and salt from .env.
 * @param {string} facilityId - e.g. "1", "2", "3" (matches
 *                              FACILITY_<id>_HOSPITAL / _DOCTOR_ID / _SALT).
 * @param {{taskId?, round?, payload?}} ctx - proof context the contract checks:
 *        createTask (newTaskId, 0, initialWeightsHash), joinTask (taskId, 0, 0),
 *        submitUpdate (taskId, currentRound, weightsHash).
 * @returns {Promise<{proof:{a,b,c}, facilityHashId:string}>}
 */
async function generateFacilityProof(facilityId, { taskId = 0, round = 0, payload = '0' } = {}) {
  const hospital = process.env[`FACILITY_${facilityId}_HOSPITAL`];
  const doctorId = process.env[`FACILITY_${facilityId}_DOCTOR_ID`];
  const salt     = process.env[`FACILITY_${facilityId}_SALT`];
  if (!hospital || !doctorId || !salt) {
    throw new Error(
      `Missing FACILITY_${facilityId}_HOSPITAL / _DOCTOR_ID / _SALT in .env`
    );
  }
  const identity = `${hospital}:${doctorId}`;

  console.log(`    🔐 Generating ZK proof for facility ${facilityId} (${identity})...`);
  const res = await fetch(`${ZK_PROVE_URL}/prove`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      identity, salt,
      task_id: String(taskId), round: String(round), payload: String(payload),
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`ZK prove service error (${res.status}): ${text}`);
  }
  const { proof, pub } = await res.json();

  // Convert snarkjs format → Solidity Verifier format
  // Note: pi_b requires index swap (snarkjs convention vs solidity)
  const proofStruct = {
    a: [BigInt(proof.pi_a[0]), BigInt(proof.pi_a[1])],
    b: [
      [BigInt(proof.pi_b[0][1]), BigInt(proof.pi_b[0][0])],
      [BigInt(proof.pi_b[1][1]), BigInt(proof.pi_b[1][0])],
    ],
    c: [BigInt(proof.pi_c[0]), BigInt(proof.pi_c[1])],
  };
  const facilityHashId = ethers.toBeHex(BigInt(pub[0]), 32);

  return { proof: proofStruct, facilityHashId };
}

/* ─── Health Check ─── */
app.get('/api/fl/health', async (req, res) => {
  try {
    const flHealth = await flFetch('/health');
    const fl = await getFlContract();
    const taskCount = await fl.taskCount();
    res.json({
      status: 'ok',
      flService: flHealth.status,
      contractAddress: await fl.getAddress(),
      taskCount: Number(taskCount),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/* ─── Phase 2: Read-only service-status fan-out ───────────────────────
   GET /api/fl/services
   Probes the five process ports with short-timeout GET requests and
   reports up/offline status. Used by the thesis research dashboard
   (FE/research_dashboard.html) in Live Mode.

   Strictly read-only:
     - No /prove, /train, /aggregate, /model/init, /hospitals calls.
     - No Sepolia transactions.
     - No Filecoin / Lighthouse uploads.
     - No secrets, RPC URLs, or env values exposed in the response.
   ─────────────────────────────────────────────────────────────────── */
async function probeHealth(url, timeoutMs = 2000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { method: 'GET', signal: controller.signal });
    return { ok: r.ok };
  } catch (e) {
    return { ok: false };
  } finally {
    clearTimeout(timer);
  }
}

app.get('/api/fl/services', async (req, res) => {
  const checkedAt = new Date().toISOString();

  // FL bridge probes itself directly (this very process is serving the request).
  const [loginRes, registerRes, zkRes, flSvcRes] = await Promise.allSettled([
    probeHealth('http://localhost:3000/', 2000),
    probeHealth('http://localhost:3001/', 2000),
    probeHealth('http://localhost:7000/health', 2000),
    probeHealth('http://localhost:7001/health', 2000),
  ]);

  const ok = (r) => r.status === 'fulfilled' && r.value && r.value.ok === true;

  const services = {
    login: {
      name: 'Login server',
      port: 3000,
      ok: ok(loginRes),
      message: ok(loginRes)
        ? 'Login server :3000 — up'
        : 'Login server :3000 — offline',
    },
    register: {
      name: 'Register server',
      port: 3001,
      ok: ok(registerRes),
      message: ok(registerRes)
        ? 'Register server :3001 — up'
        : 'Register server :3001 — offline',
    },
    fl_bridge: {
      name: 'FL bridge',
      port: 3002,
      ok: true,
      message: 'FL bridge :3002 — up',
    },
    zk: {
      name: 'ZK proof service',
      port: 7000,
      ok: ok(zkRes),
      message: ok(zkRes)
        ? 'ZK proof service :7000 — up'
        : 'ZK service is offline. Start FastAPI service on port 7000.',
    },
    fl_service: {
      name: 'FL service',
      port: 7001,
      ok: ok(flSvcRes),
      message: ok(flSvcRes)
        ? 'FL service :7001 — up'
        : 'FL service is offline. Start FL service on port 7001.',
    },
  };

  res.json({ mode: 'live', checked_at: checkedAt, services });
});

/* ─── Phase 3: Data Collection (webcam capture + video upload) ────────
   Saves user-provided webcam recordings or uploaded video files to disk
   under ./collected_data/. Strictly local file I/O only. Never:
     - extracts features
     - runs inference
     - calls /train, /prove, /aggregate, /model/init, /hospitals
     - sends a Sepolia transaction
     - uploads to Filecoin / Lighthouse
   ─────────────────────────────────────────────────────────────────── */
const COLLECTED_DIR = path.join(__dirname, 'collected_data');
const COLLECT_MAX_BYTES = 50 * 1024 * 1024;
const COLLECT_MIME_TO_EXT = {
  'video/webm': 'webm',
  'video/mp4':  'mp4',
};
const COLLECT_FILENAME_REGEX = /^[a-zA-Z0-9_\-]+\.(webm|mp4)$/;

if (!fs.existsSync(COLLECTED_DIR)) {
  fs.mkdirSync(COLLECTED_DIR, { recursive: true });
}

app.post(
  '/api/collect/upload',
  express.raw({
    type: ['video/webm', 'video/mp4'],
    limit: COLLECT_MAX_BYTES,
  }),
  (req, res) => {
    try {
      const ctype = (req.headers['content-type'] || '')
        .split(';')[0]
        .trim()
        .toLowerCase();
      const ext = COLLECT_MIME_TO_EXT[ctype];
      if (!ext) {
        return res.status(400).json({
          error: 'Unsupported content-type. Expected video/webm or video/mp4.',
        });
      }
      const body = req.body;
      if (!Buffer.isBuffer(body) || body.length === 0) {
        return res.status(400).json({ error: 'Empty or invalid body.' });
      }
      if (body.length > COLLECT_MAX_BYTES) {
        return res.status(413).json({ error: 'File too large.' });
      }
      const filename = `${Date.now()}_${crypto.randomBytes(4).toString('hex')}.${ext}`;
      const filePath = path.join(COLLECTED_DIR, filename);
      fs.writeFileSync(filePath, body);
      console.log(`📥 Collected: ${filename} (${body.length} bytes)`);
      res.json({
        filename,
        size: body.length,
        savedAt: new Date().toISOString(),
      });
    } catch (err) {
      console.error('❌ Collect upload failed:', err.message);
      res.status(500).json({ error: err.message });
    }
  },
);

app.get('/api/collect/list', (req, res) => {
  try {
    const files = fs.readdirSync(COLLECTED_DIR)
      .filter((f) => COLLECT_FILENAME_REGEX.test(f))
      .map((f) => {
        const st = fs.statSync(path.join(COLLECTED_DIR, f));
        return {
          filename: f,
          size: st.size,
          savedAt: st.mtime.toISOString(),
        };
      })
      .sort((a, b) => b.savedAt.localeCompare(a.savedAt))
      .slice(0, 5);
    res.json({ files });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/collect/file/:name', (req, res) => {
  const name = req.params.name;
  if (!COLLECT_FILENAME_REGEX.test(name)) {
    return res.status(400).json({ error: 'Invalid filename.' });
  }
  const resolved = path.resolve(COLLECTED_DIR, name);
  if (
    !resolved.startsWith(COLLECTED_DIR + path.sep) &&
    resolved !== COLLECTED_DIR
  ) {
    return res.status(400).json({ error: 'Invalid filename.' });
  }
  if (!fs.existsSync(resolved)) {
    return res.status(404).json({ error: 'Not found.' });
  }
  const ext = path.extname(name).slice(1);
  res.setHeader('Content-Type', ext === 'mp4' ? 'video/mp4' : 'video/webm');
  fs.createReadStream(resolved).pipe(res);
});

/* ─── 1) Create Task ─── */
app.post('/api/fl/task/create', async (req, res) => {
  console.log('📝 Creating FL task...');
  try {
    const { description, totalRounds, minParticipants, facilityId } = req.body;

    if (!description || !totalRounds || !minParticipants || !facilityId) {
      return res.status(400).json({
        error: 'Missing description, totalRounds, minParticipants, or facilityId',
      });
    }

    // 1. Init model via FL Service
    console.log('  ① Init model...');
    const initResult = await flFetch('/model/init');

    // 2. Encrypt (AES-256) + upload initial weights to Filecoin
    console.log('  ② Encrypt + upload initial weights to Filecoin...');
    const weightsData = await buildEncryptedWeightsPayload(
      initResult.weights_key,
      'fl_weights',
      initResult.sha256,
    );
    const filecoinResult = await pushToFilecoin(weightsData, `fl_init_${Date.now()}.json`, 'FL initial model weights (AES-256 encrypted)');

    const fl = await getFlContract();
    const signer = await getSigner();

    const modelHashBytes32 = ethers.zeroPadValue('0x' + initResult.sha256.slice(0, 64), 32);
    const weightsHashBytes32 = ethers.zeroPadValue('0x' + initResult.sha256.slice(0, 64), 32);

    // 3. Generate ZK proof for creator facility, bound to the new task + initial weights
    console.log(`  ③ Generating ZK proof for facility ${facilityId}...`);
    const newTaskId = (await fl.taskCount()) + 1n;
    const { proof, facilityHashId } = await generateFacilityProof(facilityId, {
      taskId: newTaskId, payload: weightsHashBytes32,
    });

    // 4. Submit to blockchain
    console.log('  ④ Registering on blockchain...');

    const taskParams = {
      modelHash: modelHashBytes32,
      description,
      totalRounds,
      minParticipants,
      initialModelCID: filecoinResult.cid || '',
      initialWeightsHash: weightsHashBytes32,
    };

    const tx = await fl.connect(signer).createTask(taskParams, proof, facilityHashId);
    const receipt = await tx.wait();

    // Parse taskId from event
    const taskCreatedEvent = receipt.logs.find(log => {
      try { return fl.interface.parseLog(log)?.name === 'TaskCreated'; } catch { return false; }
    });
    const taskId = taskCreatedEvent
      ? Number(fl.interface.parseLog(taskCreatedEvent).args[0])
      : Number(await fl.taskCount());

    console.log(`  ✅ Task #${taskId} created by facility ${facilityHashId} (tx: ${tx.hash})`);

    res.json({
      taskId,
      creatorFacility: facilityHashId,
      txHash: tx.hash,
      initialModelKey: initResult.weights_key,
      filecoinCID: filecoinResult.cid,
      gasUsed: Number(receipt.gasUsed),
    });
  } catch (err) {
    console.error('❌ Create task failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

/* ─── 2) Join Task ─── */
app.post('/api/fl/task/:id/join', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    const { facilityId } = req.body;

    if (!facilityId) {
      return res.status(400).json({ error: 'Missing facilityId' });
    }

    console.log(`📝 Facility ${facilityId} joining task #${taskId}...`);

    const { proof, facilityHashId } = await generateFacilityProof(facilityId, { taskId });

    const fl = await getFlContract();
    const signer = await getSigner();

    const tx = await fl.connect(signer).joinTask(taskId, proof, facilityHashId);
    const receipt = await tx.wait();

    console.log(`  ✅ Facility ${facilityHashId} joined task #${taskId}`);
    res.json({
      taskId,
      facilityHashId,
      txHash: tx.hash,
      gasUsed: Number(receipt.gasUsed),
    });
  } catch (err) {
    console.error('❌ Join failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

/* ─── 3) Start Round ─── */
app.post('/api/fl/task/:id/start-round', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    console.log(`🔄 Starting round for task #${taskId}...`);

    const fl = await getFlContract();
    const signer = await getSigner();

    const tx = await fl.connect(signer).startRound(taskId);
    const receipt = await tx.wait();

    const info = await fl.getTaskInfo(taskId);
    const currentRound = Number(info[4]); // currentRound

    console.log(`  ✅ Round ${currentRound} started`);
    res.json({ taskId, currentRound, txHash: tx.hash, gasUsed: Number(receipt.gasUsed) });
  } catch (err) {
    console.error('❌ Start round failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

/* ─── 4) Train (local training + submit to blockchain) ─── */
app.post('/api/fl/task/:id/train', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    const { hospitalId, facilityId, epochs } = req.body;

    if (!hospitalId || !facilityId) {
      return res.status(400).json({ error: 'Missing hospitalId or facilityId' });
    }

    console.log(`🏋️ Facility ${facilityId} training task #${taskId} on ${hospitalId}...`);

    // 1. Get current global model CID from blockchain
    console.log('  ① Reading global model from blockchain...');
    const fl = await getFlContract();
    const taskModel = await fl.getTaskModel(taskId);
    const globalModelCID = taskModel[1];

    // 2. Train via FL Service
    console.log(`  ② Training on ${hospitalId} (${epochs || 5} epochs)...`);
    const trainResult = await flFetch('/train', {
      method: 'POST',
      body: JSON.stringify({
        hospital_id: hospitalId,
        global_weights_key: null,
        epochs: epochs || 5,
      }),
    });

    // 3. Encrypt (AES-256) + upload weights to Filecoin.
    //    Sensitive metadata (hospital, facility, accuracy) is encrypted with
    //    the weights so the Filecoin object leaks neither the model nor the
    //    contributing facility.
    console.log('  ③ Encrypt + upload weights to Filecoin...');
    const weightsData = await buildEncryptedWeightsPayload(
      trainResult.weights_key,
      'fl_local_weights',
      trainResult.weights_sha256,
      { hospital: hospitalId, facilityId, accuracy: trainResult.accuracy },
    );
    const filecoinResult = await pushToFilecoin(
      weightsData,
      `fl_local_${Date.now()}.json`,
      'Local model weights (AES-256 encrypted)'
    );

    // 4. Generate ZK proof for this facility, bound to (task, current round, weights)
    console.log(`  ④ Generating ZK proof for facility ${facilityId}...`);
    const weightsHash = ethers.zeroPadValue('0x' + trainResult.weights_sha256.slice(0, 64), 32);
    const currentRound = (await fl.getTaskInfo(taskId))[4];
    const { proof, facilityHashId } = await generateFacilityProof(facilityId, {
      taskId, round: currentRound, payload: weightsHash,
    });

    // 5. Submit to blockchain
    console.log('  ⑤ Submitting to blockchain...');
    const signer = await getSigner();
    const accuracyInt = Math.round(trainResult.accuracy * 10000);

    const tx = await fl.connect(signer).submitUpdate(
      taskId,
      weightsHash,
      filecoinResult.cid || trainResult.weights_key,
      accuracyInt,
      trainResult.data_size,
      proof,
      facilityHashId,
    );
    const receipt = await tx.wait();

    console.log(`  ✅ Training done: accuracy=${trainResult.accuracy}, submitted by ${facilityHashId}`);

    res.json({
      taskId,
      hospitalId,
      facilityId,
      facilityHashId,
      accuracy: trainResult.accuracy,
      dataSize: trainResult.data_size,
      loss: trainResult.loss,
      weightsKey: trainResult.weights_key,
      filecoinCID: filecoinResult.cid,
      txHash: tx.hash,
      gasUsed: Number(receipt.gasUsed),
      elapsedMs: trainResult.elapsed_ms,
    });
  } catch (err) {
    console.error('❌ Train failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

/* ─── 5) Aggregate ─── */
app.post('/api/fl/task/:id/aggregate', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    console.log(`🔗 Aggregating task #${taskId}...`);

    const fl = await getFlContract();
    const signer = await getSigner();

    // 1. Read all submissions for current round
    const info = await fl.getTaskInfo(taskId);
    const currentRound = Number(info[4]);
    const updateCount = Number(await fl.getRoundUpdateCount(taskId, currentRound));

    console.log(`  ① Round ${currentRound}: ${updateCount} submissions`);

    // 2. Collect weight keys and data sizes from FL Service
    // (In full implementation, would download from Filecoin CIDs)
    const { weightKeys, dataSizes } = req.body;

    if (!weightKeys || !dataSizes || weightKeys.length < 2) {
      return res.status(400).json({ error: 'Need weightKeys and dataSizes (min 2)' });
    }

    // 3. Aggregate via FL Service
    console.log(`  ② Aggregating ${weightKeys.length} updates...`);
    const aggResult = await flFetch('/aggregate', {
      method: 'POST',
      body: JSON.stringify({
        weight_keys: weightKeys,
        data_sizes: dataSizes,
        test_hospital: req.body.testHospital || null,
      }),
    });

    // 4. Encrypt (AES-256) + upload global model to Filecoin
    console.log('  ③ Encrypt + upload global model to Filecoin...');
    const globalData = await buildEncryptedWeightsPayload(
      aggResult.global_weights_key,
      'fl_global_model',
      aggResult.global_weights_sha256,
      {
        taskId,
        round: currentRound,
        participants: aggResult.participant_count,
        totalSamples: aggResult.total_samples,
      },
    );
    const filecoinResult = await pushToFilecoin(
      globalData,
      `fl_global_task${taskId}_r${currentRound}_${Date.now()}.json`,
      `Global model task #${taskId} round ${currentRound} (AES-256 encrypted)`
    );

    // 5. Submit aggregation to blockchain
    console.log('  ④ Submitting aggregation to blockchain...');
    const globalHash = ethers.zeroPadValue('0x' + aggResult.global_weights_sha256.slice(0, 64), 32);

    const tx = await fl.connect(signer).submitAggregation(
      taskId,
      filecoinResult.cid || aggResult.global_weights_key,
      globalHash,
    );
    const receipt = await tx.wait();

    // Check if task completed
    const updatedInfo = await fl.getTaskInfo(taskId);
    const status = Number(updatedInfo[2]); // getTaskInfo returns (creator, description, status, ...)

    console.log(`  ✅ Aggregation done. Status: ${['RECRUITING','TRAINING','AGGREGATING','COMPLETED','CANCELLED'][status]}`);

    res.json({
      taskId,
      round: currentRound,
      globalWeightsKey: aggResult.global_weights_key,
      globalAccuracy: aggResult.global_accuracy,
      participantCount: aggResult.participant_count,
      totalSamples: aggResult.total_samples,
      filecoinCID: filecoinResult.cid,
      txHash: tx.hash,
      gasUsed: Number(receipt.gasUsed),
      taskStatus: ['RECRUITING', 'TRAINING', 'AGGREGATING', 'COMPLETED', 'CANCELLED'][status],
    });
  } catch (err) {
    console.error('❌ Aggregate failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

/* ─── 6) Get Tasks List ─── */
app.get('/api/fl/tasks', async (req, res) => {
  try {
    const fl = await getFlContract();
    const count = Number(await fl.taskCount());

    const tasks = [];
    for (let i = 1; i <= count; i++) {
      const info = await fl.getTaskInfo(i);
      const participantCount = Number(await fl.getTaskParticipantCount(i));
      tasks.push({
        id: i,
        creator: info[0],
        description: info[1],
        status: ['RECRUITING', 'TRAINING', 'AGGREGATING', 'COMPLETED', 'CANCELLED'][Number(info[2])],
        totalRounds: Number(info[3]),
        currentRound: Number(info[4]),
        minParticipants: Number(info[5]),
        participantCount,
      });
    }

    res.json({ tasks, total: count });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/* ─── 7) Get Task Detail ─── */
app.get('/api/fl/task/:id', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    const fl = await getFlContract();

    const info = await fl.getTaskInfo(taskId);
    const model = await fl.getTaskModel(taskId);
    const participantCount = Number(await fl.getTaskParticipantCount(taskId));
    const participants = await fl.getParticipants(taskId);

    // Get updates for current round
    const currentRound = Number(info[4]);
    const updateCount = currentRound > 0
      ? Number(await fl.getRoundUpdateCount(taskId, currentRound))
      : 0;

    const updates = [];
    for (let i = 0; i < updateCount; i++) {
      const u = await fl.getRoundUpdate(taskId, currentRound, i);
      updates.push({
        facilityHashId: u[0],
        weightsHash: u[1],
        weightsCID: u[2],
        accuracy: Number(u[3]) / 10000,
        dataSize: Number(u[4]),
        submittedAt: new Date(Number(u[5]) * 1000).toISOString(),
      });
    }

    res.json({
      id: taskId,
      creator: info[0],
      description: info[1],
      status: ['RECRUITING', 'TRAINING', 'AGGREGATING', 'COMPLETED', 'CANCELLED'][Number(info[2])],
      totalRounds: Number(info[3]),
      currentRound,
      minParticipants: Number(info[5]),
      participantCount,
      participants,
      modelHash: model[0],
      globalModelCID: model[1],
      globalWeightsHash: model[2],
      creatorFacility: model[3],
      currentRoundUpdates: updates,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/* ─── 8) Cancel Task ─── */
app.post('/api/fl/task/:id/cancel', async (req, res) => {
  try {
    const taskId = parseInt(req.params.id);
    const fl = await getFlContract();
    const signer = await getSigner();

    const tx = await fl.connect(signer).cancelTask(taskId);
    const receipt = await tx.wait();

    res.json({ taskId, txHash: tx.hash, gasUsed: Number(receipt.gasUsed) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/* ─── Start Server ─── */
app.listen(PORT, () =>
  console.log(`🚀 FL Server listening on http://localhost:${PORT}`)
);
