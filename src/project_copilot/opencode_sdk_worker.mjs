import { pathToFileURL } from "node:url"

function unwrap(result) {
  if (result && typeof result === "object" && "error" in result && result.error) {
    throw new Error("OpenCode SDK request failed")
  }
  if (result && typeof result === "object" && "data" in result) {
    return result.data
  }
  return result
}

async function readInput() {
  let value = ""
  process.stdin.setEncoding("utf8")
  for await (const chunk of process.stdin) value += chunk
  const parsed = JSON.parse(value)
  if (!parsed || typeof parsed !== "object") throw new TypeError("request must be an object")
  return parsed
}

function eventProperties(event) {
  if (!event || typeof event !== "object") return {}
  if (event.properties && typeof event.properties === "object") return event.properties
  if (event.data && typeof event.data === "object") return event.data
  return {}
}

async function enforceStepLimit(runtime, request, sessionID, signal) {
  const subscription = await runtime.client.event.subscribe(
    {
      query: { directory: request.cwd },
      signal,
      throwOnError: true,
    },
  )
  const assistantMessages = new Set()
  for await (const event of subscription.stream) {
    if (event?.type !== "message.updated") continue
    const properties = eventProperties(event)
    if (properties.sessionID !== sessionID) continue
    const info = properties.info
    if (!info || info.role !== "assistant" || !info.id) continue
    assistantMessages.add(info.id)
    if (assistantMessages.size <= request.max_steps) continue
    void runtime.client.session.abort(
      {
        path: { id: sessionID },
        query: { directory: request.cwd },
        throwOnError: false,
      },
    ).catch(() => {})
    throw new Error(
      `OpenCode exceeded the configured ${request.max_steps}-step budget without a final answer`,
    )
  }
  return new Promise(() => {})
}

function turnTimeout(runtime, request, sessionID) {
  let timer
  const promise = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      void runtime.client.session.abort(
        {
          path: { id: sessionID },
          query: { directory: request.cwd },
          throwOnError: false,
        },
      ).catch(() => {})
      reject(new Error("OpenCode turn exceeded the configured time budget"))
    }, request.turn_timeout_ms)
  })
  return { promise, cancel: () => clearTimeout(timer) }
}

async function run() {
  const request = await readInput()
  process.chdir(request.cwd)
  const sdk = await import(pathToFileURL(request.sdk_entrypoint).href)
  if (typeof sdk.createOpencode !== "function") {
    throw new TypeError("OpenCode SDK entrypoint is invalid")
  }
  const controller = new AbortController()
  const eventController = new AbortController()
  let runtime
  let timeout
  try {
    runtime = await sdk.createOpencode({
      hostname: "127.0.0.1",
      port: 0,
      signal: controller.signal,
      timeout: request.startup_timeout_ms,
      config: request.config,
    })
    const mcpStatus = unwrap(
      await runtime.client.mcp.status(
        {
          query: { directory: request.cwd },
          throwOnError: true,
        },
      ),
    )
    if (!mcpStatus?.hvac || mcpStatus.hvac.status !== "connected") {
      throw new Error(`HVAC MCP preflight failed: ${JSON.stringify(mcpStatus?.hvac ?? null)}`)
    }
    const created = unwrap(
      await runtime.client.session.create(
        {
          query: { directory: request.cwd },
          body: { title: "Project Copilot private analysis" },
          throwOnError: true,
        },
      ),
    )
    const sessionID = created.id
    if (!sessionID) throw new Error("OpenCode SDK did not create a session")
    const promptText = request.output_mode === "text_json"
      ? `${request.prompt}\n\nFinal response contract: return only one JSON object matching this schema, with no markdown fence or commentary: ${JSON.stringify(request.output_schema)}`
      : request.prompt
    const promptBody = {
      model: {
        providerID: request.provider_id,
        modelID: request.model,
      },
      agent: request.agent,
      parts: [{ type: "text", text: promptText }],
    }
    if (request.output_mode === "native_schema") {
      promptBody.format = {
        type: "json_schema",
        schema: request.output_schema,
      }
    }
    const promptPromise = runtime.client.session.prompt(
      {
        path: { id: sessionID },
        query: { directory: request.cwd },
        body: promptBody,
        throwOnError: true,
      },
    )
    timeout = turnTimeout(runtime, request, sessionID)
    const promptResult = unwrap(
      await Promise.race([
        promptPromise,
        enforceStepLimit(
          runtime,
          request,
          sessionID,
          eventController.signal,
        ),
        timeout.promise,
      ]),
    )
    const messages = request.output_mode === "text_json"
      ? unwrap(
          await runtime.client.session.messages(
            {
              path: { id: sessionID },
              query: { directory: request.cwd },
              throwOnError: true,
            },
          ),
        )
      : [promptResult]
    process.stdout.write(JSON.stringify({ prompt_result: promptResult, messages }))
  } finally {
    timeout?.cancel()
    eventController.abort()
    if (runtime) runtime.server.close()
    controller.abort()
  }
}

try {
  await run()
} catch (error) {
  process.stderr.write(
    `${error instanceof Error ? error.stack ?? error.message : String(error)}\n`,
  )
  process.stdout.write(
    JSON.stringify({
      error: {
        message: "OpenCode SDK worker failed",
        kind: error instanceof Error ? error.name : "Error",
      },
    }),
  )
  process.exitCode = 1
}
