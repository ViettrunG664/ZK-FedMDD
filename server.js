// Login Server - Port 3000

import cors from 'cors';
import express from 'express';
import bodyParser from 'body-parser';
import { exec } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/* ─── config ─────────────────────────────────────────── */
const PORT = process.env.PORT || 3000;
const app = express();
app.use(express.json());

app.use(cors({
  origin: ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:8080'],
  methods: ['POST', 'GET'],
  allowedHeaders: ['Content-Type'],
}));

app.use(express.static(path.join(__dirname, 'FE')));
app.get('/', (_, res) =>
  res.sendFile(path.join(__dirname, 'FE', 'login.html'))
);

// Timeout wrapper for exec commands
async function execWithTimeout(cmd, timeout = 30000) {
  const options = { env: { ...process.env, HARDHAT_NETWORK: 'sepolia' } };
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`Command timeout after ${timeout}ms`)), timeout)
  );
  return Promise.race([execAsync(cmd, options), timeoutPromise]);
}

// Request timeout middleware
app.use((req, res, next) => {
  req.setTimeout(180000); // 3 minutes
  res.setTimeout(180000);
  next();
});

/* ─── LOGIN API (WITH ROLE VERIFICATION) ─────────────────────────────────────────── */
app.post('/api/login', async (req, res) => {
  const startTime = Date.now();
  console.log('🔐 Login started');

  try {
    const { hospital, doctorId, salt, role } = req.body;

    if (!hospital || !/^BV\.[A-Z0-9]+$/.test(hospital)) {
      return res.status(400).json({ error: 'Invalid hospital code (expected BV.<NAME>)' });
    }
    if (!doctorId || !/^[A-Z0-9]{8}$/.test(doctorId)) {
      return res.status(400).json({ error: 'Invalid doctor ID (8 uppercase alphanumeric)' });
    }
    if (!salt) {
      return res.status(400).json({ error: 'Missing salt' });
    }
    if (!role || !['patient', 'doctor'].includes(role)) {
      return res.status(400).json({ error: 'Invalid role (must be "patient" or "doctor")' });
    }

    const identity = `${hospital}:${doctorId}`;

    // Step 1: Generate ZK proof via FastAPI
    console.log(`🔹 Step 1: Generating ZK proof for ${role} (${identity})...`);
    const proveStartTime = Date.now();

    const fastApiResp = await fetch('http://localhost:7000/prove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity, salt })
    });

    if (!fastApiResp.ok) {
      const errorText = await fastApiResp.text();
      console.error('❌ FastAPI proof generation failed:', errorText);
      return res.status(fastApiResp.status).json({
        error: 'ZK proof generation failed',
        details: errorText
      });
    }

    const proveData = await fastApiResp.json();
    const proveElapsed = Date.now() - proveStartTime;
    console.log(`✅ ZK proof generated in ${proveElapsed}ms`);

    // Step 2: Verify credentials AND role via zklogin
    console.log(`🔹 Step 2: Verifying credentials and role (${role})...`);
    const zkStartTime = Date.now();

    const cmd = `node scripts/zklogin.js --hospital ${hospital} --doctor-id ${doctorId} --salt ${salt} --role ${role}`;
    const { stdout } = await execWithTimeout(cmd, 60000);

    const zkElapsed = Date.now() - zkStartTime;
    console.log(`✅ Credentials verified in ${zkElapsed}ms`);

    // Parse zklogin result
    let zkResult;
    try {
      const lines = stdout.split('\n').filter(Boolean);
      const lastLine = lines[lines.length - 1];
      zkResult = JSON.parse(lastLine);
    } catch (e) {
      console.error('Failed to parse zklogin output:', stdout);
      zkResult = { result: stdout };
    }

    // Check if zklogin failed (role mismatch or invalid proof)
    if (zkResult.ok === false) {
      return res.status(401).json({
        error: zkResult.error || 'Login failed',
        details: 'Role mismatch or invalid credentials'
      });
    }

    const totalElapsed = Date.now() - startTime;
    console.log(`✅ Login completed in ${totalElapsed}ms`);

    // Return combined result
    return res.json({
      message: 'Login successful',
      proof: proveData.proof,
      publicSignals: proveData.pub,
      ...zkResult,
      timings: {
        proof: proveElapsed,
        verification: zkElapsed,
        total: totalElapsed
      }
    });

  } catch (err) {
    const totalElapsed = Date.now() - startTime;
    console.error(`❌ Login failed after ${totalElapsed}ms:`, err.message);
    return res.status(500).json({
      error: err.message || 'Login failed',
      details: err.toString()
    });
  }
});


/* ─── LEGACY ENDPOINTS (kept for backward compatibility) ─────────────────────────────────────────── */
// Deprecated: Use /api/login instead
app.post('/api/login/prove', async (req, res) => {
  console.warn('⚠️ /api/login/prove is deprecated. Use /api/login instead.');
  const body = { cccd: req.body.cccd, salt: req.body.salt };

  try {
    const fastApiResp = await fetch('http://localhost:7000/prove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    const raw = await fastApiResp.text();
    if (!fastApiResp.ok) {
      return res.status(fastApiResp.status).type('application/json').send(raw);
    }
    return res.type('application/json').send(raw);
  } catch (err) {
    console.error('Error:', err.message);
    return res.status(500).json({ error: err.message });
  }
});

// Deprecated: Use /api/login instead
app.post('/api/login/zklogin', async (req, res) => {
  console.warn('⚠️ /api/login/zklogin is deprecated. Use /api/login instead.');
  const body = { cccd: req.body.cccd, salt: req.body.salt };
  const cmd = `node scripts/zklogin.js --cccd ${body.cccd} --salt ${body.salt}`;

  try {
    const { stdout, stderr } = await execWithTimeout(cmd, 30000);
    return res.json({ result: stdout });
  } catch (err) {
    console.error('Error:', err.message);
    return res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () =>
  console.log(`🚀 Login Server listening on http://localhost:${PORT}`)
);