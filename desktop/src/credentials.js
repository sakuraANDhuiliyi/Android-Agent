"use strict";

/**
 * Main-process credential helpers. Tokens are encrypted with Electron
 * safeStorage when available; otherwise they live only in this process
 * until quit.
 */

function credentialKey(baseUrl) {
  return String(baseUrl || "").slice(0, 2048);
}

function createSessionStore() {
  const session = new Map();
  return {
    get(key) {
      return session.has(key) ? session.get(key) : null;
    },
    set(key, token) {
      if (token) session.set(key, String(token));
      else session.delete(key);
    },
    has(key) {
      return session.has(key);
    },
    clear(key) {
      session.delete(key);
    },
  };
}

/**
 * @param {object} deps
 * @param {() => boolean} deps.isEncryptionAvailable
 * @param {(plain: string) => string} deps.encryptBase64
 * @param {(encoded: string) => string} deps.decryptBase64
 * @param {() => Promise<Record<string, string>>} deps.load
 * @param {(items: Record<string, string>) => Promise<void>} deps.save
 * @param {{ get: Function, set: Function, has: Function, clear: Function }} [deps.session]
 */
function createCredentialStore(deps) {
  const session = deps.session || createSessionStore();

  async function get(baseUrl) {
    const key = credentialKey(baseUrl);
    if (session.has(key)) return session.get(key) || "";
    if (!deps.isEncryptionAvailable()) return "";
    const items = await deps.load();
    const encoded = items[key];
    if (!encoded) return "";
    try {
      return deps.decryptBase64(encoded);
    } catch (_) {
      return "";
    }
  }

  async function set(baseUrl, token) {
    const key = credentialKey(baseUrl);
    if (!deps.isEncryptionAvailable()) {
      session.set(key, token);
      return { persisted: false, sessionOnly: true };
    }
    session.clear(key);
    const items = await deps.load();
    if (token) items[key] = deps.encryptBase64(String(token));
    else delete items[key];
    await deps.save(items);
    return { persisted: true, sessionOnly: false };
  }

  return { get, set, session };
}

module.exports = {
  credentialKey,
  createSessionStore,
  createCredentialStore,
};
