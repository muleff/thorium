import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import test from 'node:test';
import { CdpClient, classifyGpu, frameSummary, waitForPage } from '../tools/gpu_probe.mjs';

// Real loopback WebSocket transport; only small CDP test frames are needed.
async function serverFor(handler) {
  const server = createServer();
  const sockets = new Set();
  server.on('connection', socket => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  server.on('upgrade', (request, socket) => {
    const accept = createHash('sha1')
      .update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
      .digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    let buffer = Buffer.alloc(0);
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 6) {
        let length = buffer[1] & 127;
        let offset = 2;
        if (length === 127) { socket.destroy(); return; }
        if (length === 126) {
          if (buffer.length < 8) return;
          length = buffer.readUInt16BE(2);
          offset = 4;
        }
        if (buffer.length < offset + 4 + length) return;
        const opcode = buffer[0] & 15;
        const mask = buffer.subarray(offset, offset + 4);
        const payload = Buffer.from(buffer.subarray(offset + 4, offset + 4 + length));
        buffer = buffer.subarray(offset + 4 + length);
        for (let i = 0; i < payload.length; ++i) payload[i] ^= mask[i % 4];
        if (opcode === 8) { socket.end(Buffer.from([0x88, 0])); return; }
        const send = value => {
          const encoded = Buffer.from(JSON.stringify(value));
          assert.ok(encoded.length < 65536);
          const header = encoded.length < 126 ? Buffer.from([0x81, encoded.length]) :
            Buffer.from([0x81, 126, encoded.length >> 8, encoded.length & 255]);
          socket.write(Buffer.concat([header, encoded]));
        };
        handler(JSON.parse(payload.toString()), send, socket);
      }
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return {
    url: `ws://127.0.0.1:${server.address().port}`,
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise(resolve => server.close(resolve));
    },
  };
}

test('CDP correlates concurrent responses arriving in reverse order', async () => {
  const requests = [];
  const server = await serverFor((message, send) => {
    requests.push(message);
    if (requests.length === 2) {
      send({ id: requests[1].id, result: { value: 2 } });
      send({ id: requests[0].id, result: { value: 1 } });
    }
  });
  const client = await CdpClient.connect(server.url);
  try {
    const results = await Promise.all([client.call('First'), client.call('Second')]);
    assert.deepEqual(results, [{ value: 1 }, { value: 2 }]);
  } finally { client.close(); await server.close(); }
});

test('CDP surfaces protocol errors', async () => {
  const server = await serverFor((message, send) => send({ id: message.id, error: { message: 'Denied' } }));
  const client = await CdpClient.connect(server.url);
  try { await assert.rejects(client.call('Denied'), /Denied/); }
  finally { client.close(); await server.close(); }
});

test('CDP rejects unanswered commands within the deadline', async () => {
  const server = await serverFor(() => {});
  const client = await CdpClient.connect(server.url);
  try { await assert.rejects(client.call('Slow', {}, undefined, 30), /timed out/); }
  finally { client.close(); await server.close(); }
});

test('CDP rejects pending requests when transport closes', async () => {
  const server = await serverFor((_message, _send, socket) => socket.destroy());
  const client = await CdpClient.connect(server.url);
  try { await assert.rejects(client.call('Closed'), /CDP transport (closed|error)/); }
  finally { client.close(); await server.close(); }
});

test('CDP rejects malformed response envelopes without an uncaught exception', async () => {
  const server = await serverFor((_message, send) => send(null));
  const client = await CdpClient.connect(server.url);
  try { await assert.rejects(client.call('Malformed'), /Invalid CDP/); }
  finally { client.close(); await server.close(); }
});

test('CDP rejects connections outside loopback', async () => {
  await assert.rejects(CdpClient.connect('ws://example.com:9222'), /loopback/);
});

test('probe waits for tab creation and DOM readiness before measuring', async () => {
  let targetCalls = 0, domCalls = 0;
  const server = await serverFor((message, send) => {
    let result = {};
    if (message.method === 'Target.getTargets') result = { targetInfos: ++targetCalls < 2 ? [] : [{ type: 'page', url: 'about:blank', targetId: 't' }] };
    else if (message.method === 'Target.attachToTarget') result = { sessionId: 's' };
    else if (message.method === 'Runtime.evaluate') result = { result: { value: ++domCalls > 1 } };
    send({ id: message.id, result });
  });
  const client = await CdpClient.connect(server.url);
  try {
    assert.deepEqual(await waitForPage(client), { targetId: 't', sessionId: 's' });
    assert.equal(targetCalls, 2);
    assert.equal(domCalls, 2);
  } finally { client.close(); await server.close(); }
});

test('GPU classification does not mistake software ANGLE for hardware', () => {
  assert.equal(classifyGpu({ auxAttributes: { glRenderer: 'ANGLE (SwiftShader Device)' }, featureStatus: { gpu_compositing: 'enabled' } }).hardware, false);
  assert.equal(classifyGpu({ auxAttributes: { glRenderer: 'llvmpipe' }, featureStatus: {} }).hardware, false);
  assert.equal(classifyGpu({ auxAttributes: { glRenderer: 'ANGLE (Intel, UHD Graphics Direct3D11)' }, featureStatus: { gpu_compositing: 'enabled', rasterization: 'enabled' } }).hardware, true);
  assert.equal(classifyGpu({ auxAttributes: {}, featureStatus: {} }).hardware, false);
});

test('frame summary reports tail latency and rejects invalid samples', () => {
  assert.deepEqual(frameSummary([10, 20, 30, 40]), { count: 4, medianMs: 25, p95Ms: 40, maxMs: 40 });
  assert.throws(() => frameSummary([]), /samples/);
  assert.throws(() => frameSummary([16, NaN]), /samples/);
});
