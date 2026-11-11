// utils/filecoin.js
// Filecoin/Lighthouse integration for uploading data to distributed storage
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const LIGHTHOUSE_UPLOAD_URL = 'https://node.lighthouse.storage/api/v0/add';
const FILECOIN_DIR = path.join(__dirname, '..', 'data', 'filecoin');

/**
 * Poseidon-like hash function (SHA-256 based)
 * Matches the sample project's implementation
 * @param {Buffer} data - Data to hash
 * @returns {string} Hex hash
 */
function poseidonLikeHash(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

/**
 * Upload file to Lighthouse using official SDK
 * @param {string} apiKey - Lighthouse API key
 * @param {Buffer} fileBuffer - File content as buffer
 * @param {string} filename - Original filename
 * @returns {Promise<object>} Lighthouse API response
 */
async function lighthouseUpload(apiKey, fileBuffer, filename) {
  // Use official Lighthouse SDK (more reliable than raw API)
  const lighthouse = (await import('@lighthouse-web3/sdk')).default;

  try {
    // Convert buffer to string for uploadText
    const textData = fileBuffer.toString('utf8');

    // Use SDK's uploadText method (as per official docs)
    const response = await lighthouse.uploadText(textData, apiKey, filename);

    // SDK returns { data: { Name, Hash, Size } }
    return response.data;
  } catch (error) {
    console.error('❌ Lighthouse SDK upload failed:', error.message);
    throw error;
  }
}

/**
 * Generate mock CID and deal_id for testing without API key
 * @param {Buffer} fileBuffer - File content
 * @param {string} filename - Original filename for uniqueness
 * @returns {object} { deal_id: number, cid: string }
 */
function mockDeal(fileBuffer, filename) {
  // Add timestamp and filename to ensure uniqueness
  const timestamp = Date.now().toString();
  const uniqueData = Buffer.concat([
    fileBuffer,
    Buffer.from(filename),
    Buffer.from(timestamp)
  ]);

  // Use hash for deal_id
  const dealHash = crypto.createHash('sha256').update(uniqueData).digest();
  const deal_id = parseInt(dealHash.toString('hex').slice(0, 12), 16) % (10 ** 12);

  // Use different portion for CID
  const cid = poseidonLikeHash(uniqueData);

  return { deal_id, cid };
}

/**
 * Save upload record to filecoin directory
 * @param {object} record - Upload metadata
 * @param {string} filename - Output filename
 */
function saveUploadRecord(record, filename) {
  // Ensure directory exists
  if (!fs.existsSync(FILECOIN_DIR)) {
    fs.mkdirSync(FILECOIN_DIR, { recursive: true });
  }

  const filepath = path.join(FILECOIN_DIR, filename);
  fs.writeFileSync(filepath, JSON.stringify(record, null, 2), 'utf8');

  console.log(`📦 Filecoin record saved: ${filename}`);
}

/**
 * Main function to upload data to Filecoin via Lighthouse
 * @param {object|string} data - Data to upload (will be JSON stringified)
 * @param {string} originalFilename - Original filename for the data
 * @param {string} description - Description of what this data is
 * @returns {Promise<object>} { provider, cid, deal_id, filename, timestamp }
 */
export async function pushToFilecoin(data, originalFilename, description = '') {
  try {
    // Convert data to JSON buffer
    const jsonContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    const fileBuffer = Buffer.from(jsonContent, 'utf8');

    const apiKey = process.env.LIGHTHOUSE_API_KEY;
    const timestamp = new Date().toISOString();

    let record;
    let outputFilename;

    if (apiKey && apiKey.trim().length > 0) {
      // Real upload to Lighthouse
      console.log(`🚀 Uploading to Lighthouse: ${originalFilename}`);

      try {
        const sdkResponse = await lighthouseUpload(apiKey, fileBuffer, originalFilename);

        // SDK returns: { Name, Hash, Size }
        const cid = sdkResponse.Hash;
        const size = sdkResponse.Size;

        record = {
          provider: 'lighthouse',
          cid: cid,
          deal_id: null, // SDK doesn't provide deal_id immediately
          original_filename: originalFilename,
          description: description,
          timestamp: timestamp,
          size: size,
          name: sdkResponse.Name
        };

        outputFilename = `lighthouse_${cid}.json`;

      } catch (uploadError) {
        console.error('❌ Lighthouse upload failed, falling back to mock mode');
        // Fall back to mock mode
        const { deal_id, cid } = mockDeal(fileBuffer, originalFilename);

        record = {
          provider: 'mock',
          cid: cid,
          deal_id: deal_id,
          original_filename: originalFilename,
          description: description,
          timestamp: timestamp,
          status: 'uploaded (mock - lighthouse failed)',
          error: uploadError.message
        };

        outputFilename = `deal_${deal_id}.json`;
      }

    } else {
      // Mock mode (no API key)
      console.log(`🎭 Mock upload (no API key): ${originalFilename}`);

      const { deal_id, cid } = mockDeal(fileBuffer, originalFilename);

      record = {
        provider: 'mock',
        cid: cid,
        deal_id: deal_id,
        original_filename: originalFilename,
        description: description,
        timestamp: timestamp,
        status: 'uploaded (mock)'
      };

      outputFilename = `deal_${deal_id}.json`;
    }

    // Save record to disk
    saveUploadRecord(record, outputFilename);

    console.log(`✅ Filecoin upload complete: ${record.provider} (deal_id=${record.deal_id}, cid=${record.cid})`);

    return {
      provider: record.provider,
      cid: record.cid,
      deal_id: record.deal_id,
      filename: outputFilename,
      timestamp: timestamp
    };

  } catch (error) {
    console.error('❌ pushToFilecoin error:', error.message);
    throw error;
  }
}
