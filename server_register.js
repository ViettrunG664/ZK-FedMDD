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

const PORT = process.env.REGISTER_PORT || 3001;
const app = express();
app.use(express.json());

app.use(cors({
  origin: ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:8080'],
  methods: ['POST', 'GET'],
  allowedHeaders: ['Content-Type'],
}));

app.use(express.static(path.join(__dirname, 'FE')));

app.get('/', (_, res) =>
  res.sendFile(path.join(__dirname, 'FE', 'register.html'))
);

async function execWithTimeout(cmd, timeout = 30000) {
  const options = { env: { ...process.env, HARDHAT_NETWORK: 'sepolia' } };
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`Command timeout after ${timeout}ms`)), timeout)
  );
  return Promise.race([execAsync(cmd, options), timeoutPromise]);
}

app.use((req, res, next) => {
  req.setTimeout(180000);
  res.setTimeout(180000);
  next();
});

app.post('/api/register', async (req, res) => {
  const startTime = Date.now();
  console.log('📝 Registration started');

  try {
    const { hospital, doctorId, role } = req.body;

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

    console.log('🔹 Step 2: Registering on blockchain...');
    const { hashId } = secretObj;
    const regCmd = `node scripts/03_register.js --hashId ${hashId} --role ${role}`;
    await execWithTimeout(regCmd, 120000);

    console.log('🔹 Step 3: Uploading to Filecoin...');
    let filecoinResult = null;

    try {
      console.log('  🔐 Encrypting identity...');
      const encryptedIdentity = encryptAES(identity);

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
