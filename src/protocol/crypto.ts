import { ECDH_PRIVATE_KEY_HEX } from './constants';
import { fromHex, toHex } from './utils';

export interface SessionKeys {
  aesKey: Uint8Array;    // First 16 bytes of shared secret
  iv: Uint8Array;        // Bytes 16-31 of shared secret (CBC IV or GCM nonce base)
  sharedSecret: Uint8Array;
}

// Convert a raw 32-byte private key to a JWK for Web Crypto ECDH
async function importPrivateKey(privateKeyHex: string): Promise<CryptoKey> {
  const privateKeyBytes = fromHex(privateKeyHex);

  // We need to construct the PKCS8/JWK format for the private key
  // For ECDH with P-256, the private key is a 32-byte scalar
  // Using JWK format for Web Crypto API
  const jwk: JsonWebKey = {
    kty: 'EC',
    crv: 'P-256',
    // We need to derive the public key from the private key
    // For now, we'll compute it during key generation
    d: uint8ArrayToBase64url(privateKeyBytes),
    // x and y will be computed by the API
    x: '', // placeholder - will be filled
    y: '', // placeholder - will be filled
  };

  // Unfortunately Web Crypto doesn't let us import just d without x,y
  // We need to compute the public point. Let's use a different approach:
  // Generate a keypair and use the known private key bytes
  // Actually, we need to compute the public key from the private key.
  // Let's use the SubtleCrypto approach with raw import

  // Alternative: import as raw private key via PKCS8
  // PKCS8 DER encoding for P-256 ECDH key
  const pkcs8Header = fromHex(
    '30818706010100301306072a8648ce3d020106082a8648ce3d030107046d306b0201010420'
  );
  const pkcs8Suffix = fromHex('a14403420004');

  // We need the public key point to create a full PKCS8 key
  // Let's try a different approach - compute ECDH via a raw approach
  // Actually, the simplest Web Crypto approach: import the PKCS8 without the public key part
  const pkcs8NoPublic = fromHex('302e0201010420');
  const pkcs8Params = fromHex('a00706052b8104000a'); // This is secp256k1, we need P-256
  // P-256 OID: 1.2.840.10045.3.1.7 = 06 08 2a 86 48 ce 3d 03 01 07

  // Let's build a proper PKCS8 for P-256
  // See RFC 5915 + RFC 5480
  const ecPrivateKey = buildECPrivateKeyPKCS8(privateKeyBytes);

  return await crypto.subtle.importKey(
    'pkcs8',
    ecPrivateKey,
    { name: 'ECDH', namedCurve: 'P-256' },
    true,
    ['deriveBits']
  );
}

function buildECPrivateKeyPKCS8(privateKeyBytes: Uint8Array): ArrayBuffer {
  // PKCS#8 wrapping of an EC private key for P-256
  // PrivateKeyInfo ::= SEQUENCE {
  //   version Version,
  //   privateKeyAlgorithm AlgorithmIdentifier,
  //   privateKey OCTET STRING
  // }

  // AlgorithmIdentifier for P-256 ECDH:
  // SEQUENCE { OID 1.2.840.10045.2.1 (ecPublicKey), OID 1.2.840.10045.3.1.7 (P-256) }
  const algorithmId = fromHex('301306072a8648ce3d020106082a8648ce3d030107');

  // ECPrivateKey ::= SEQUENCE {
  //   version INTEGER (1),
  //   privateKey OCTET STRING
  // }
  // Minimal without public key
  const ecPrivateKeyInner = new Uint8Array([
    0x30, 0x22 + privateKeyBytes.length,
    0x02, 0x01, 0x01, // version = 1
    0x04, privateKeyBytes.length, // OCTET STRING
    ...privateKeyBytes
  ]);

  // Wrap in OCTET STRING for PKCS8
  const privateKeyOctetString = new Uint8Array([
    0x04, ecPrivateKeyInner.length,
    ...ecPrivateKeyInner
  ]);

  // Version = 0
  const version = new Uint8Array([0x02, 0x01, 0x00]);

  // Full PKCS8 SEQUENCE
  const innerLength = version.length + algorithmId.length + privateKeyOctetString.length;
  const pkcs8 = new Uint8Array([
    0x30, 0x81, innerLength,
    ...version,
    ...algorithmId,
    ...privateKeyOctetString
  ]);

  return pkcs8.buffer;
}

export async function generateECDHKeyPair(): Promise<{ privateKey: CryptoKey; publicKey: CryptoKey; publicKeyRaw: Uint8Array }> {
  // Try to import the known private key first
  try {
    const privateKey = await importPrivateKey(ECDH_PRIVATE_KEY_HEX);
    const publicKeyRaw = await exportPublicKeyRaw(privateKey);
    // We need a separate public key object
    const publicKey = await crypto.subtle.importKey(
      'raw',
      publicKeyRaw,
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      []
    );
    return { privateKey, publicKey, publicKeyRaw: new Uint8Array(publicKeyRaw) };
  } catch (e) {
    console.warn('Failed to import hardcoded private key, generating new keypair:', e);
    // Fallback: generate a fresh keypair
    const keyPair = await crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      ['deriveBits']
    );
    const publicKeyRaw = new Uint8Array(
      await crypto.subtle.exportKey('raw', keyPair.publicKey)
    );
    return {
      privateKey: keyPair.privateKey,
      publicKey: keyPair.publicKey,
      publicKeyRaw
    };
  }
}

async function exportPublicKeyRaw(privateKey: CryptoKey): Promise<ArrayBuffer> {
  // Export as JWK to get x, y coordinates, then reconstruct raw
  const jwk = await crypto.subtle.exportKey('jwk', privateKey);
  if (!jwk.x || !jwk.y) throw new Error('Missing public key coordinates');
  const x = base64urlToUint8Array(jwk.x);
  const y = base64urlToUint8Array(jwk.y);
  const raw = new Uint8Array(65);
  raw[0] = 0x04; // uncompressed point
  raw.set(x, 1);
  raw.set(y, 33);
  return raw.buffer;
}

export async function deriveSharedSecret(
  privateKey: CryptoKey,
  devicePublicKeyRaw: Uint8Array
): Promise<SessionKeys> {
  // Import the device's public key
  const devicePublicKey = await crypto.subtle.importKey(
    'raw',
    devicePublicKeyRaw,
    { name: 'ECDH', namedCurve: 'P-256' },
    false,
    []
  );

  // Derive shared secret (32 bytes)
  const sharedBits = await crypto.subtle.deriveBits(
    { name: 'ECDH', public: devicePublicKey },
    privateKey,
    256
  );

  const sharedSecret = new Uint8Array(sharedBits);
  console.log('[Crypto] Shared secret:', toHex(sharedSecret));

  return {
    aesKey: sharedSecret.slice(0, 16),
    iv: sharedSecret.slice(16, 32),
    sharedSecret
  };
}

export async function decryptAesCbc(
  data: Uint8Array,
  key: Uint8Array,
  iv: Uint8Array
): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    'raw', key, { name: 'AES-CBC' }, false, ['decrypt']
  );

  try {
    const decrypted = await crypto.subtle.decrypt(
      { name: 'AES-CBC', iv },
      cryptoKey,
      data
    );
    return new Uint8Array(decrypted);
  } catch {
    // Try without padding removal (manual PKCS7)
    // Pad data to block boundary if needed
    const blockSize = 16;
    let padded = data;
    if (data.length % blockSize !== 0) {
      const paddedLen = Math.ceil(data.length / blockSize) * blockSize;
      padded = new Uint8Array(paddedLen);
      padded.set(data);
    }
    const cryptoKeyNoPad = await crypto.subtle.importKey(
      'raw', key, { name: 'AES-CBC' }, false, ['decrypt']
    );
    const decrypted = await crypto.subtle.decrypt(
      { name: 'AES-CBC', iv },
      cryptoKeyNoPad,
      padded
    );
    return new Uint8Array(decrypted);
  }
}

export async function encryptAesCbc(
  data: Uint8Array,
  key: Uint8Array,
  iv: Uint8Array
): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    'raw', key, { name: 'AES-CBC' }, false, ['encrypt']
  );
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-CBC', iv },
    cryptoKey,
    data
  );
  return new Uint8Array(encrypted);
}

export async function decryptAesGcm(
  data: Uint8Array,
  key: Uint8Array,
  nonce: Uint8Array,
  authData: Uint8Array
): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    'raw', key, { name: 'AES-GCM' }, false, ['decrypt']
  );

  // Last 16 bytes are the GCM tag
  const ciphertext = data.slice(0, data.length - 16);
  const tag = data.slice(data.length - 16);
  const combined = new Uint8Array(ciphertext.length + tag.length);
  combined.set(ciphertext);
  combined.set(tag, ciphertext.length);

  const decrypted = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: nonce, additionalData: authData, tagLength: 128 },
    cryptoKey,
    combined
  );
  return new Uint8Array(decrypted);
}

function uint8ArrayToBase64url(bytes: Uint8Array): string {
  const base64 = btoa(String.fromCharCode(...bytes));
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function base64urlToUint8Array(base64url: string): Uint8Array {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - base64.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
