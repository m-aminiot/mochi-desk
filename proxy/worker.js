function relayWebSocket(client, upstream) {
  const closeBoth = (source, target, event) => {
    try {
      if (target.readyState === WebSocket.OPEN || target.readyState === WebSocket.CONNECTING) {
        target.close(event.code || 1000, event.reason || "Peer closed");
      }
    } catch {
      // The peer may already be closed.
    }
  };

  // Keep binary audio frames as raw bytes. Newer Workers compatibility dates
  // may otherwise expose them as Blob objects, which are not forwarded intact.
  client.binaryType = "arraybuffer";
  upstream.binaryType = "arraybuffer";
  client.accept({ allowHalfOpen: true });
  upstream.accept({ allowHalfOpen: true });

  client.addEventListener("message", (event) => {
    try {
      upstream.send(event.data);
    } catch {
      closeBoth(client, upstream, { code: 1011, reason: "Upstream send failed" });
    }
  });

  upstream.addEventListener("message", (event) => {
    try {
      client.send(event.data);
    } catch {
      closeBoth(upstream, client, { code: 1011, reason: "Client send failed" });
    }
  });

  client.addEventListener("close", (event) => closeBoth(client, upstream, event));
  upstream.addEventListener("close", (event) => closeBoth(upstream, client, event));
  client.addEventListener("error", () => closeBoth(client, upstream, { code: 1011, reason: "Client socket error" }));
  upstream.addEventListener("error", () => closeBoth(upstream, client, { code: 1011, reason: "Upstream socket error" }));
}

export default {
  async fetch(request) {
    const upstreamUrl = new URL(request.url);
    upstreamUrl.hostname = "generativelanguage.googleapis.com";

    const headers = new Headers(request.headers);
    headers.delete("cf-connecting-ip");
    headers.delete("x-real-ip");

    try {
      const upstreamRequest = new Request(upstreamUrl.toString(), {
        method: request.method,
        headers,
        body: request.body,
        redirect: "follow",
      });
      const response = await fetch(upstreamRequest);
      const isWebSocket = request.headers.get("Upgrade")?.toLowerCase() === "websocket";

      if (isWebSocket && response.status === 101 && response.webSocket) {
        const pair = new WebSocketPair();
        const [client, server] = Object.values(pair);
        relayWebSocket(server, response.webSocket);
        return new Response(null, { status: 101, webSocket: client });
      }

      const responseHeaders = new Headers(response.headers);
      responseHeaders.set("Access-Control-Allow-Origin", "*");
      responseHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      responseHeaders.set("Access-Control-Allow-Headers", "*");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    } catch (error) {
      return new Response(JSON.stringify({ error: String(error?.message || error) }), {
        status: 502,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      });
    }
  },
};
