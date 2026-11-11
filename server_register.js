// Registration Server - Port 3001
import cors from 'cors';
import express from 'express';
import bodyParser from 'body-parser';
import { exec } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import 'dotenv/config';
import { pushToFilecoin } from './utils/filecoin.js';
import { encryptAES } from './utils/encryption.js';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/* ─── Config ─────────────────────────────────────────── */
const PORT = process.env.REGISTER_PORT || 3001;
const app = express();
app.use(express.json());

app.use(cors({
  origin: ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:8080'],
  methods: ['POST', 'GET'],
  allowedHeaders: ['Content-Type'],
}));

app.use(express.static(path.join(__dirname, 'FE')));

// Default route serves the register page
app.get('/', (_, res) =>
  res.sendFile(path.join(__dirname, 'FE', 'register.html'))
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

/* ─── REGISTRATION API (UNIFIED FOR BOTH DOCTOR & PATIENT) ─────────────────────────────────────────── */
app.post('/api/register', async (req, res) => {
  const startTime = Date.now();
  console.log('📝 Registration started');

  try {
    const { hospital, doctorId, role } = req.body;

    // Validation
    if (!hospital || !/^BV\.[A-Z0-9]+$/.test(hospital)) {
      return res.status(400).json({ error: 'Invalid hospital code (expected BV.<NAME>, uppercase alphanumeric)' });
    }
    if (!doctorId || !/^[A-Z0-9]{8}$/.test(doctorId)) {
      return res.status(400).json({ error: 'Invalid doctor ID (must be 8 uppercase alphanumeric chars)' });
    }
    if (!role || !['patient', 'doctor'].includes(role)) {
      return res.status(400).json({ error: 'Invalid role (must be "patient" or "doctor")' });
    }

    const identity = `${hospital}:${doctorId}`;

    // Step 1: Generate secret
    console.log(`🔹 Step 1: Generating secret for ${role} (${identity})...`);
    const cmd = `node scripts/01_gen_secret.js --hospital ${hospital} --doctor-id ${doctorId} --role ${role}`;
    const { stdout: stdout1 } = await execWithTimeout(cmd, 10000);

    const lines = stdout1.split('\n').filter(Boolean);
    const lastLine = lines[lines.length - 1];
    let secretObj = {};

    try {
      secretObj = JSON.parse(lastLine);
    } catch (e) {
      console.error('Failed to parse gen_secret output:', lastLine);
      return res.status(500).json({
        error: 'Failed to parse secret generation output',
        raw: lastLine
      });
    }

    // Step 2: Register on blockchain
    console.log('🔹 Step 2: Registering on blockchain...');
    const { hashId } = secretObj;
    const regCmd = `node scripts/03_register.js --hashId ${hashId} --role ${role}`;
    await execWithTimeout(regCmd, 120000);

    // Step 3: Upload to Filecoin (combined: encrypted identity, salt, role)
    console.log('🔹 Step 3: Uploading to Filecoin...');
    let filecoinResult = null;

    try {
      // 3a. Encrypt identity string (hospital:doctorId)
      console.log('  🔐 Encrypting identity...');
      const encryptedIdentity = encryptAES(identity);

      // 3b. Combine all data into single JSON
      const combinedData = {
        identity: {
          encrypted: encryptedIdentity.encrypted,
          iv: encryptedIdentity.iv
        },
        hospital: hospital,
        salt: secretObj.derivedSalt,
        role: role,
        hashId: hashId
      };

      // 3c. Upload combined data to Filecoin (single file)
      console.log('  📤 Uploading combined data (identity + salt + role)...');
      filecoinResult = await pushToFilecoin(
        combinedData,
        `user_data_${hashId.slice(0, 12)}.json`,
        `User data for ${identity} (${role})`
      );

      console.log('✅ Filecoin upload complete');
    } catch (filecoinError) {
      console.error('⚠️  Filecoin upload error (non-blocking):', filecoinError.message);
      filecoinResult = { error: filecoinError.message };
    }

    const elapsed = Date.now() - startTime;
    console.log(`✅ ${role.charAt(0).toUpperCase() + role.slice(1)} registered in ${elapsed}ms`);

    return res.json({
      message: `${role.charAt(0).toUpperCase() + role.slice(1)} registered successfully!`,
      salt: secretObj.derivedSalt,
      hashId: secretObj.hashId,
      role: role,
      filecoin: filecoinResult
    });

  } catch (err) {
    const elapsed = Date.now() - startTime;
    console.error(`❌ Registration failed after ${elapsed}ms:`, err.message);
    return res.status(500).json({
      error: err.message || 'Registration failed',
      details: err.stderr || err.toString()
    });
  }
});

app.listen(PORT, () =>
  console.log(`🚀 Registration Server listening on http://localhost:${PORT}`)
);
