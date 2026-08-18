const assert = require("assert");
const {
  credentialKey,
  createSessionStore,
  createCredentialStore,
} = require("../src/credentials");

function run() {
  assert.strictEqual(credentialKey("http://127.0.0.1:8000").startsWith("http://"), true);

  const disk = {};
  const store = createCredentialStore({
    isEncryptionAvailable: () => false,
    encryptBase64: () => {
      throw new Error("should not encrypt");
    },
    decryptBase64: () => {
      throw new Error("should not decrypt");
    },
    load: async () => ({ ...disk }),
    save: async (items) => {
      Object.keys(disk).forEach((k) => delete disk[k]);
      Object.assign(disk, items);
    },
  });

  return store.set("http://local", "tok-session").then(async (result) => {
    assert.strictEqual(result.sessionOnly, true);
    assert.strictEqual(result.persisted, false);
    assert.deepStrictEqual(disk, {});
    assert.strictEqual(await store.get("http://local"), "tok-session");
    assert.strictEqual(await store.get("http://other"), "");

    const encrypted = createCredentialStore({
      isEncryptionAvailable: () => true,
      encryptBase64: (plain) => Buffer.from(plain).toString("base64"),
      decryptBase64: (encoded) => Buffer.from(encoded, "base64").toString("utf8"),
      load: async () => ({ ...disk }),
      save: async (items) => {
        Object.keys(disk).forEach((k) => delete disk[k]);
        Object.assign(disk, items);
      },
      session: createSessionStore(),
    });
    const persisted = await encrypted.set("http://local", "tok-disk");
    assert.strictEqual(persisted.persisted, true);
    assert.strictEqual(persisted.sessionOnly, false);
    assert.ok(disk["http://local"]);
    assert.strictEqual(await encrypted.get("http://local"), "tok-disk");
    console.log(`ok - ${module.filename}`);
  });
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
