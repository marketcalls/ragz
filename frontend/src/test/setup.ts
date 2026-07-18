import '@testing-library/jest-dom/vitest';

// jsdom's AbortController/AbortSignal are a different class than the one
// Node's native fetch/Request (undici) checks against internally, even
// though `signal instanceof globalThis.AbortSignal` reports true — so any
// jsdom-created AbortSignal makes `new Request(url, { signal })` throw
// "Expected signal to be an instance of AbortSignal". React Router's data
// router unconditionally attaches one to the Request it builds for every
// navigation, so this bites on every route change under jsdom even though
// real browsers never hit the mismatch. Tests here don't assert on request
// cancellation, so dropping the signal before constructing the Request is a
// safe, test-environment-only shim.
const OriginalRequest = globalThis.Request;

class JsdomSafeRequest extends OriginalRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.signal) {
      const { signal, ...rest } = init;
      void signal;
      super(input, rest);
    } else {
      super(input, init);
    }
  }
}

globalThis.Request = JsdomSafeRequest as unknown as typeof Request;

// Node 22+ ships an experimental global `localStorage` backed by a file
// (--localstorage-file) that silently resolves to `undefined` when no file is
// configured. That getter shadows jsdom's own storage implementation before
// tests ever run, so any code under test that reads/writes localStorage
// (theme + workspace persistence) crashes with "Cannot read properties of
// undefined". Replace it with a minimal synchronous, in-memory Storage
// implementation — same contract, browser-like behavior, test-scoped only.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

Object.defineProperty(globalThis, 'localStorage', {
  value: new MemoryStorage(),
  configurable: true,
  writable: true,
});
