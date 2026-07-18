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
