export interface SessionKeys {
  aesKey: Uint8Array;
  iv: Uint8Array;
  sharedSecret: Uint8Array;
}

// Web Crypto requires ArrayBuffer-backed inputs; copying avoids SharedArrayBuffer types.
function bytes(value: Uint8Array): ArrayBuffer {
  return Uint8Array.from(value).buffer;
}

export async function generateECDHKeyPair(): Promise<{ privateKey: CryptoKey; publicKey: CryptoKey; publicKeyRaw: Uint8Array }> {
  const pair = await crypto.subtle.generateKey(
    { name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']
  );
  return {
    privateKey: pair.privateKey,
    publicKey: pair.publicKey,
    publicKeyRaw: new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey)),
  };
}

export async function generateFreshECDHKeyPair(): Promise<{ privateKey: CryptoKey; publicKeyRaw: Uint8Array }> {
  const { privateKey, publicKeyRaw } = await generateECDHKeyPair();
  return { privateKey, publicKeyRaw };
}

export async function deriveSharedSecret(privateKey: CryptoKey, devicePublicKeyRaw: Uint8Array): Promise<SessionKeys> {
  const devicePublicKey = await crypto.subtle.importKey(
    'raw', bytes(devicePublicKeyRaw), { name: 'ECDH', namedCurve: 'P-256' }, false, []
  );
  const sharedSecret = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'ECDH', public: devicePublicKey }, privateKey, 256
  ));
  return {
    aesKey: sharedSecret.slice(0, 16),
    iv: sharedSecret.slice(16, 32),
    sharedSecret,
  };
}

export async function decryptAesCbc(data: Uint8Array, key: Uint8Array, iv: Uint8Array): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey('raw', bytes(key), 'AES-CBC', false, ['decrypt']);
  return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-CBC', iv: bytes(iv) }, cryptoKey, bytes(data)));
}

export async function encryptAesCbc(data: Uint8Array, key: Uint8Array, iv: Uint8Array): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey('raw', bytes(key), 'AES-CBC', false, ['encrypt']);
  return new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-CBC', iv: bytes(iv) }, cryptoKey, bytes(data)));
}

export async function decryptAesGcm(data: Uint8Array, key: Uint8Array, nonce: Uint8Array, authData: Uint8Array): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey('raw', bytes(key), 'AES-GCM', false, ['decrypt']);
  return new Uint8Array(await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: bytes(nonce), additionalData: bytes(authData), tagLength: 128 },
    cryptoKey, bytes(data)
  ));
}

export async function encryptAesGcm(data: Uint8Array, key: Uint8Array, nonce: Uint8Array, authData: Uint8Array): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey('raw', bytes(key), 'AES-GCM', false, ['encrypt']);
  return new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: bytes(nonce), additionalData: bytes(authData), tagLength: 128 },
    cryptoKey, bytes(data)
  ));
}
