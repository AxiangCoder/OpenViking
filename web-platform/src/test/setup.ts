import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll } from "vitest";

beforeAll(() => {
  if (typeof globalThis.scrollTo !== "function") {
    globalThis.scrollTo = (() => () => undefined) as typeof scrollTo;
  }
});

afterEach(() => {
  cleanup();
  globalThis.localStorage.clear();
});
