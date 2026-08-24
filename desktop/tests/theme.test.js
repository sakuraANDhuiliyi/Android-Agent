const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/theme.js"), "utf8");
const mediaListeners = [];
const storage = {};
const themeClasses = new Set();
const context = {
  window: {
    matchMedia: () => ({
      matches: true,
      addEventListener: (_type, fn) => mediaListeners.push(fn),
    }),
    dispatchEvent() {},
  },
  document: {
    documentElement: {
      dataset: {},
      style: {},
      classList: {
        toggle(name, force) {
          if (force === undefined) {
            if (themeClasses.has(name)) themeClasses.delete(name);
            else themeClasses.add(name);
          } else if (force) {
            themeClasses.add(name);
          } else {
            themeClasses.delete(name);
          }
        },
        contains: (name) => themeClasses.has(name),
      },
    },
  },
  localStorage: {
    getItem: (key) => storage[key] || null,
    setItem: (key, value) => { storage[key] = value; },
  },
  CustomEvent: class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = init?.detail; }
  },
};
vm.createContext(context);
vm.runInNewContext(code, context);

const theme = context.window.ThemeManager;
assert.strictEqual(theme.resolveMode("system", true), "light");
assert.strictEqual(theme.resolveMode("system", false), "dark");
assert.strictEqual(theme.resolveMode("light", false), "light");
assert.strictEqual(theme.normalizeMode("unknown"), "dark");
assert.strictEqual(theme.getMode(), "dark");
// Dark default must activate TheoKit's `.dark` token set on <html>.
assert.strictEqual(context.document.documentElement.classList.contains("dark"), true);

theme.setMode("system");
assert.strictEqual(storage["android-agent-desktop-theme"], "system");
assert.strictEqual(theme.getResolved(), "light");
assert.strictEqual(context.document.documentElement.dataset.theme, "light");
// Light resolution removes `.dark` so @theokit/ui tokens flip to light values.
assert.strictEqual(context.document.documentElement.classList.contains("dark"), false);

console.log("theme.test: OK");
