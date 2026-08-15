import { afterEach, beforeEach, vi } from 'vitest';

/**
 * Recharts' `ResponsiveContainer` never renders its children under jsdom: it
 * waits for a `ResizeObserver` to report a real size, and jsdom ships none —
 * so charts render an empty 0x0 placeholder and tests can't assert on
 * anything but "didn't throw". Call this once per chart test file to stub a
 * fixed container size, so primitives render real SVG geometry (bars, arcs,
 * legend items, …) that tests can query against.
 *
 * Scoped to `beforeEach`/`afterEach` so the stub never leaks into unrelated
 * test files even though it patches globals.
 */
export function mockResponsiveContainerSize(width = 400, height = 300): void {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    class StubResizeObserver {
      observe(): void {
        /* no-op: ResponsiveContainer reads the size synchronously via
           getBoundingClientRect right after constructing the observer. */
      }

      unobserve(): void {}

      disconnect(): void {}
    }
    globalThis.ResizeObserver = StubResizeObserver as unknown as typeof ResizeObserver;

    // Only the ResponsiveContainer's own wrapper gets a fixed size. Recharts
    // also calls getBoundingClientRect while measuring legend/tick text —
    // stubbing every element's size confuses that layout pass (margins
    // balloon, plot area collapses to 0 height). Real jsdom behavior (all
    // zeros) is left alone for everything else.
    const original = Element.prototype.getBoundingClientRect;
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: Element,
    ) {
      if (this.classList.contains('recharts-responsive-container')) {
        return {
          width,
          height,
          top: 0,
          left: 0,
          right: width,
          bottom: height,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect;
      }
      return original.call(this);
    });
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    vi.restoreAllMocks();
  });
}
