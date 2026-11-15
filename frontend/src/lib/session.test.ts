import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SESSION = { token: "t".repeat(43), user: { email: "maya@accountable.demo", name: "Maya Chen", customer_id: "cust_maya" } };

function fakeWindow(storage: Pick<Storage, "getItem" | "setItem" | "removeItem">) {
  return Object.assign(new EventTarget(), { localStorage: storage });
}

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

const blockedStorage = {
  getItem: () => { throw new Error("SecurityError"); },
  setItem: () => { throw new Error("SecurityError"); },
  removeItem: () => { throw new Error("SecurityError"); },
};

async function freshModule() {
  vi.resetModules(); // the module keeps an in-memory fallback; start each test clean
  return import("./session");
}

afterEach(() => vi.unstubAllGlobals());

describe("session", () => {
  let storage: ReturnType<typeof memoryStorage>;
  beforeEach(() => {
    storage = memoryStorage();
    vi.stubGlobal("window", fakeWindow(storage));
  });

  it("starts logged out", async () => {
    const { currentSession } = await freshModule();
    expect(currentSession()).toBeNull();
  });

  it("stores a session, announces it, and clears it", async () => {
    const { currentSession, setSession, clearSession, onSessionChange } = await freshModule();
    const listener = vi.fn();
    const stop = onSessionChange(listener);

    setSession(SESSION);
    expect(currentSession()).toEqual(SESSION);
    clearSession();
    expect(currentSession()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(2);

    stop();
    setSession(SESSION);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["not json", "{oops"],
    ["missing token", JSON.stringify({ ...SESSION, token: "" })],
    ["missing user", JSON.stringify({ token: "abc" })],
    ["wrong user shape", JSON.stringify({ ...SESSION, user: { email: 1 } })],
  ])("ignores a stored value that is %s", async (_label, raw) => {
    storage.data.set("accountable.session", raw);
    const { currentSession } = await freshModule();
    expect(currentSession()).toBeNull();
  });
});

describe("session when storage is blocked", () => {
  beforeEach(() => vi.stubGlobal("window", fakeWindow(blockedStorage)));

  it("still logs in for this page and logs out cleanly", async () => {
    const { currentSession, setSession, clearSession } = await freshModule();
    expect(currentSession()).toBeNull();
    setSession(SESSION);
    expect(currentSession()).toEqual(SESSION);
    clearSession();
    expect(currentSession()).toBeNull();
  });
});
