// Optional real Zen runtime fixture. Only the model and IM transport are synthetic.
// Load the exact checkout source through its installed tsx loader; never trust stale dist.
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import path from "node:path";
const root = process.env.IMAGENT_ZEN_ROOT;
const source = async (name) => import(pathToFileURL(path.join(root, "src", name)).href);
const { ZenAppServer } = await source("app-server.ts");
const { AgentRuntime } = await source("runtime.ts");
const { InMemoryThreadJournal } = await source("journal.ts");
const { InMemoryThreadMetadataStore } = await source("thread-metadata.ts");
const { StaticModelCatalog } = await source("model-catalog.ts");
const { ProviderRegistry } = await source("provider-registry.ts");
const { ToolEnvironment } = await source("tool.ts");
const { serveCodexWebSocket } = await source("protocol/codex/websocket.ts");
const lines = createInterface({ input: process.stdin });
const config = JSON.parse((await lines[Symbol.asyncIterator]().next()).value);
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const gates = new Map();
let nextGate;
const requests = [];
const model = {
  provider: "fake",
  async *stream(request) {
    requests.push(request);
    const gate = nextGate;
    nextGate = undefined;
    if (gate) {
      gate.arrived.resolve();
      await gate.release.promise;
    }
    if (request.signal.aborted) return;
    yield { type: "text_delta", delta: "native SDK answer" };
  },
};
const journal = new InMemoryThreadJournal();
const appServer = new ZenAppServer({
  journal,
  runtime: new AgentRuntime({ toolEnvironment: new ToolEnvironment({ runtimes: [] }) }),
  providerRegistry: new ProviderRegistry([{
    providerProfileId: "fake", adapter: model,
    modelCatalog: new StaticModelCatalog([{
      id: "fake", isDefault: true, inputModalities: ["text", "image"],
      supportedReasoningEfforts: ["medium"], defaultReasoningEffort: "medium", contextWindow: 32768,
    }]),
  }]),
  threadMetadata: new InMemoryThreadMetadataStore(),
  defaults: {
    cwd: config.cwd, providerProfileId: "fake", modelId: "fake", reasoningEffort: "medium",
    sandbox: "danger-full-access", approvalPolicy: "never",
  },
});
const server = await serveCodexWebSocket({
  appServer, zenHome: config.cwd, listen: "ws://127.0.0.1:0",
  bearerToken: config.token,
});
const report = (value) => process.stdout.write(JSON.stringify(value) + "\n");
report({ url: server.url });
try {
  for await (const line of lines) {
    const command = JSON.parse(line);
    try {
      let result = {};
      if (command.action === "hold") {
        const gate = { arrived: deferred(), release: deferred() };
        gates.set(command.key, gate);
        nextGate = gate;
      } else if (command.action === "arrived") {
        await gates.get(command.key).arrived.promise;
      } else if (command.action === "release") {
        gates.get(command.key).release.resolve();
      } else if (command.action === "snapshot") {
        const snapshot = await appServer.readThread(command.threadId);
        result = {
          approvalPolicy: snapshot.approvalPolicy,
          sandbox: snapshot.sandbox,
          items: await journal.read(command.threadId),
          modelSawImage: requests.some((request) => request.messages.some((message) =>
            Array.isArray(message.content) && message.content.some((part) => part.type === "image"))),
        };
      } else if (command.action === "close") {
        break;
      } else throw new Error("unknown fixture command");
      report(result);
    } catch (error) { report({ error: String(error) }); }
  }
} finally {
  for (const gate of gates.values()) gate.release.resolve();
  lines.close();
  await server.close();
}
