import "@testing-library/jest-dom/vitest";
import { vi, beforeEach } from "vitest";

// jsdom lacks these APIs; polyfill with no-ops / stubs.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

if (!window.scrollTo) {
  window.scrollTo = vi.fn();
}

// scrollIntoView is not implemented in jsdom
Element.prototype.scrollIntoView = vi.fn();

// localStorage exists in jsdom, but make sure it starts clean per test file.
beforeEach(() => {
  window.localStorage.clear();
});
