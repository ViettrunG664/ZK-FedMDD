// utils/encryption.js
// AES-256-CBC encryption/decryption utilities
import crypto from 'crypto';
import 'dotenv/config';

const ALGORITHM = 'aes-256-cbc';
const KEY_LENGTH = 32; // 256 bits

/**
 * Get or generate AES key from environment
 * @returns {Buffer} 32-byte AES key
 */
function getAESKey() {
  let keyHex = process.env.AES_KEY;

  if (!keyHex) {
    // Generate a random key if not provided
    console.warn('⚠️  AES_KEY not found in .env, generating random key (not persistent!)');
    keyHex = crypto.randomBytes(KEY_LENGTH).toString('hex');
  }

  // Convert hex string to Buffer
  const key = Buffer.from(keyHex, 'hex');

  if (key.length !== KEY_LENGTH) {
    throw new Error(`AES_KEY must be ${KEY_LENGTH * 2} hex characters (${KEY_LENGTH} bytes)`);
  }

  return key;
}

/**
 * Encrypt data using AES-256-CBC
 * @param {string|object} data - Data to encrypt (will be JSON stringified if object)
 * @returns {object} { encrypted: base64, iv: base64 }
 */
export function encryptAES(data) {
  try {
    const key = getAESKey();

    // Convert data to string if it's an object
    const plaintext = typeof data === 'string' ? data : JSON.stringify(data);

    // Generate random IV (Initialization Vector)
    const iv = crypto.randomBytes(16);

    // Create cipher
    const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

    // Encrypt
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

/**
 * Decrypt AES-256-CBC encrypted data
 * @param {string} encrypted - Base64 encrypted data
 * @param {string} ivBase64 - Base64 IV
 * @returns {string} Decrypted plaintext
 */
export function decryptAES(encrypted, ivBase64) {
  try {
    const key = getAESKey();
    const iv = Buffer.from(ivBase64, 'base64');

    // Create decipher
    const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);

    // Decrypt
    let decrypted = decipher.update(encrypted, 'base64', 'utf8');
    decrypted += decipher.final('utf8');

    return decrypted;
  } catch (error) {
    console.error('❌ Decryption error:', error.message);
    throw error;
  }
}

/**
 * Generate a random AES key (for setup purposes)
 * @returns {string} 64-character hex string (32 bytes)
 */
export function generateAESKey() {
  return crypto.randomBytes(KEY_LENGTH).toString('hex');
}
