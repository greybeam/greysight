import "@testing-library/jest-dom/vitest";

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = TestResizeObserver;

// Node 22+ exposes an experimental built-in `localStorage` that shadows jsdom's
// Storage and lacks setItem/getItem, breaking code under test. Install a simple
// in-memory Storage on window so localStorage behaves like a browser's.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length() {
    return this.store.size;
  }
  clear() {
    this.store.clear();
  }
  getItem(key: string) {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  key(index: number) {
    return Array.from(this.store.keys())[index] ?? null;
  }
  removeItem(key: string) {
    this.store.delete(key);
  }
  setItem(key: string, value: string) {
    this.store.set(key, String(value));
  }
}

// Only install the polyfill when jsdom's localStorage is absent or broken
// (e.g. Node 22+ experimental built-in that shadows jsdom and lacks
// getItem/setItem). When jsdom already provides a working Storage, leave it
// untouched so unrelated tests exercise real jsdom storage behaviour.
function needsPolyfill(): boolean {
  try {
    const ls = window.localStorage;
    if (!ls) return true;
    const probe = "__setup_probe__";
    ls.setItem(probe, "1");
    const ok = ls.getItem(probe) === "1";
    ls.removeItem(probe);
    return !ok;
  } catch {
    return true;
  }
}

if (needsPolyfill()) {
  Object.defineProperty(window, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
  });
}
