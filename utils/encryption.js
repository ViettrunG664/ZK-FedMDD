import crypto from 'crypto';
import 'dotenv/config';

const ALGORITHM = 'aes-256-cbc';
const KEY_LENGTH = 32;

function getAESKey() {
  let keyHex = process.env.AES_KEY;

  if (!keyHex) {
    console.warn('⚠️  AES_KEY not found in .env, generating random key (not persistent!)');
    keyHex = crypto.randomBytes(KEY_LENGTH).toString('hex');
  }

  const key = Buffer.from(keyHex, 'hex');

  if (key.length !== KEY_LENGTH) {
    throw new Error(`AES_KEY must be ${KEY_LENGTH * 2} hex characters (${KEY_LENGTH} bytes)`);
  }

  return key;
}

export function encryptAES(data) {
  try {
    const key = getAESKey();

    const plaintext = typeof data === 'string' ? data : JSON.stringify(data);

    const iv = crypto.randomBytes(16);

    const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

    let encrypted = cipher.update(plaintext, 'utf8', 'base64');
    encrypted += cipher.final('base64');

    return {
      encrypted: encrypted,
      iv: iv.toString('base64')
    };
  } catch (error) {
    console.error('❌ Encryption error:', error.message);
    throw error;
  }
}

export function decryptAES(encrypted, ivBase64) {
  try {
    const key = getAESKey();
    const iv = Buffer.from(ivBase64, 'base64');

    const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);

    let decrypted = decipher.update(encrypted, 'base64', 'utf8');
    decrypted += decipher.final('utf8');

    return decrypted;
  } catch (error) {
    console.error('❌ Decryption error:', error.message);
    throw error;
  }
}

export function generateAESKey() {
  return crypto.randomBytes(KEY_LENGTH).toString('hex');
}
