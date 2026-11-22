import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import { connect } from "node:net";
import test from "node:test";

const { streamNDJSON } = await import("../dist/web/src/server/ndjson.js");

const largeEvent = { type: "output", text: "x".repeat(256 * 1024) };

async function listen(t, handler) {
  const server = createServer(handler);
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return server.address().port;
}

/** A client that sends a request and then never reads the response, so the server's writes back up. */
function stalledClient(port) {
  const socket = connect(port, "127.0.0.1", () => {
    socket.write("POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n");
    socket.pause();
  });
  socket.on("error", () => {});
  return socket;
}

function within(promise, ms, what) {
  let timer;
  const timeout = new Promise((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} did not settle within ${ms} ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

test("#1234: a backpressured write settles when the client disconnects, leaving no listeners behind", async (t) => {
  let serverResponse;
  let backpressured;
  const blocked = new Promise((resolve) => {
    backpressured = resolve;
  });
  let streamed;
  const port = await listen(t, (_request, response) => {
    serverResponse = response;
    const write = response.write.bind(response);
    response.write = (...args) => {
      const flushed = write(...args);
      if (!flushed) backpressured();
      return flushed;
    };
    streamed = streamNDJSON(response, async (emit, signal) => {
      while (!signal.aborted) {
        await emit(largeEvent);
      }
    }, (error) => ({ type: "error", error }));
  });

  const client = stalledClient(port);
  t.after(() => client.destroy());
  await within(blocked, 5000, "backpressure");
  client.destroy();

  await within(streamed, 5000, "the stream");
  assert.equal(serverResponse.listenerCount("drain"), 0);
  assert.equal(serverResponse.listenerCount("error"), 0);
});

test("#1234: a client that left before the stream started never starts the run", async (t) => {
  let received;
  const requestReceived = new Promise((resolve) => {
    received = resolve;
  });
  let streamed;
  const streamSettled = new Promise((resolve) => {
    streamed = resolve;
  });
  let runStarted = false;
  const port = await listen(t, async (_request, response) => {
    received();
    // The route is still reading the request when the client goes away.
    await once(response, "close");
    streamed(streamNDJSON(response, async () => {
      runStarted = true;
    }, (error) => ({ type: "error", error })));
  });

  const client = stalledClient(port);
  t.after(() => client.destroy());
  await within(requestReceived, 5000, "receiving the request");
  client.destroy();

  await within(streamSettled, 5000, "the stream");
  assert.equal(runStarted, false);
});
