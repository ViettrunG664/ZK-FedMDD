import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const LIGHTHOUSE_UPLOAD_URL = 'https://node.lighthouse.storage/api/v0/add';
const FILECOIN_DIR = path.join(__dirname, '..', 'data', 'filecoin');

function poseidonLikeHash(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

async function lighthouseUpload(apiKey, fileBuffer, filename) {
  const lighthouse = (await import('@lighthouse-web3/sdk')).default;

  try {
    const textData = fileBuffer.toString('utf8');

    const response = await lighthouse.uploadText(textData, apiKey, filename);

    return response.data;
  } catch (error) {
    console.error('❌ Lighthouse SDK upload failed:', error.message);
    throw error;
  }
}

function mockDeal(fileBuffer, filename) {
  const timestamp = Date.now().toString();
  const uniqueData = Buffer.concat([
    fileBuffer,
    Buffer.from(filename),
    Buffer.from(timestamp)
  ]);

  const dealHash = crypto.createHash('sha256').update(uniqueData).digest();
  const deal_id = parseInt(dealHash.toString('hex').slice(0, 12), 16) % (10 ** 12);

  const cid = poseidonLikeHash(uniqueData);

  return { deal_id, cid };
}

function saveUploadRecord(record, filename) {
  if (!fs.existsSync(FILECOIN_DIR)) {
    fs.mkdirSync(FILECOIN_DIR, { recursive: true });
  }

  const filepath = path.join(FILECOIN_DIR, filename);
  fs.writeFileSync(filepath, JSON.stringify(record, null, 2), 'utf8');

  console.log(`📦 Filecoin record saved: ${filename}`);
}

export async function pushToFilecoin(data, originalFilename, description = '') {
  try {
    const jsonContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    const fileBuffer = Buffer.from(jsonContent, 'utf8');

    const apiKey = process.env.LIGHTHOUSE_API_KEY;
    const timestamp = new Date().toISOString();

    let record;
    let outputFilename;

    if (apiKey && apiKey.trim().length > 0) {
      console.log(`🚀 Uploading to Lighthouse: ${originalFilename}`);

      try {
        const sdkResponse = await lighthouseUpload(apiKey, fileBuffer, originalFilename);

        const cid = sdkResponse.Hash;
        const size = sdkResponse.Size;

        record = {
          provider: 'lighthouse',
          cid: cid,
          deal_id: null,
          original_filename: originalFilename,
          description: description,
          timestamp: timestamp,
          size: size,
          name: sdkResponse.Name
        };

        outputFilename = `lighthouse_${cid}.json`;

      } catch (uploadError) {
        console.error('❌ Lighthouse upload failed, falling back to mock mode');
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
