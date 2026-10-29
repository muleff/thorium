#!/usr/bin/env node
import { spawn, execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';

const execute = promisify(execFile);

export class CdpClient {
  #socket;
  #pending = new Map();
  #nextId = 0;

  constructor(socket) {
    this.#socket = socket;
    socket.addEventListener('message', event => {
      let message;
      try { message = JSON.parse(String(event.data)); }
      catch (error) { this.#rejectAll(new Error(`Invalid CDP JSON: ${error.message}`)); return; }
      if (!message || typeof message !== 'object' || Array.isArray(message)) {
        this.#rejectAll(new Error('Invalid CDP response envelope'));
        return;
      }
      const pending = this.#pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.#pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result ?? {});
    });
    socket.addEventListener('close', () => this.#rejectAll(new Error('CDP transport closed')));
    socket.addEventListener('error', () => this.#rejectAll(new Error('CDP transport error')));
  }

  static async connect(url, timeoutMs = 10000) {
    const address = new URL(url);
    if (address.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname)) {
      throw new Error('CDP endpoint must be a loopback WebSocket');
    }
    const socket = new WebSocket(url);
    const client = new CdpClient(socket);
    await new Promise((accept, reject) => {
      const finish = error => {
        clearTimeout(timer);
        socket.removeEventListener('open', opened);
        socket.removeEventListener('error', failed);
        socket.removeEventListener('close', closed);
        if (error) { socket.close(); reject(error); } else accept();
      };
      const opened = () => finish();
      const failed = () => finish(new Error('CDP connection failed'));
      const closed = () => finish(new Error('CDP connection closed'));
      const timer = setTimeout(() => finish(new Error('CDP connection timed out')), timeoutMs);
      socket.addEventListener('open', opened, { once: true });
      socket.addEventListener('error', failed, { once: true });
      socket.addEventListener('close', closed, { once: true });
    });
    return client;
  }

  call(method, params = {}, sessionId, timeoutMs = 15000) {
    if (this.#socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error('CDP transport closed'));
    const id = ++this.#nextId;
    return new Promise((accept, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs} ms`));
      }, timeoutMs);
      this.#pending.set(id, { resolve: accept, reject, timer, method });
      try { this.#socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
      catch (error) {
        clearTimeout(timer);
        this.#pending.delete(id);
        reject(error);
      }
    });
  }

  #rejectAll(error) {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
  }

  close() {
    this.#rejectAll(new Error('CDP transport closed'));
    this.#socket.close();
  }
}

export function classifyGpu(gpu) {
  const renderer = String(gpu.auxAttributes?.glRenderer ?? '');
  const software = /swiftshader|llvmpipe|softpipe|software|basic render|\bwarp\b/i.test(renderer);
  const compositing = gpu.featureStatus?.gpu_compositing;
  const hardware = renderer.length > 0 && !software && compositing === 'enabled';
  return { hardware, renderer, reason: hardware ? 'Hardware compositor reported by browser' :
    software ? 'Software renderer detected' : 'Hardware compositor could not be confirmed' };
}

export function frameSummary(samples) {
  if (!samples.length || samples.some(value => !Number.isFinite(value) || value <= 0)) {
    throw new Error('Frame samples must be nonempty, finite and positive');
  }
  const sorted = [...samples].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return { count: sorted.length,
    medianMs: sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2,
    p95Ms: sorted[Math.ceil(sorted.length * 0.95) - 1], maxMs: sorted.at(-1) };
}

export async function waitForPage(client, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let sessionId, targetId;
  while (Date.now() < deadline) {
    if (!targetId) {
      const { targetInfos } = await client.call('Target.getTargets', {}, undefined, Math.max(1, deadline - Date.now()));
      targetId = targetInfos.find(target => target.type === 'page' && target.url === 'about:blank')?.targetId;
      if (targetId) {
        ({ sessionId } = await client.call('Target.attachToTarget', { targetId, flatten: true }));
        await client.call('Page.bringToFront', {}, sessionId);
      }
    }
    if (sessionId) {
      const state = await client.call('Runtime.evaluate', {
        expression: "document.readyState==='complete'&&!!document.body", returnByValue: true,
      }, sessionId, Math.max(1, deadline - Date.now()));
      if (state.result?.value === true) return { targetId, sessionId };
    }
    await delay(100);
  }
  throw new Error('Browser blank page or DOM startup timed out');
}

async function processMemory(ids) {
  try {
    if (!ids.length || ids.some(id => !Number.isSafeInteger(id) || id <= 0)) throw new Error('Invalid process IDs');
    if (process.platform === 'win32') {
      const script = `$ErrorActionPreference='Stop'; $p=@(Get-Process -Id ${ids.join(',')} -ErrorAction Stop); ` +
        `@{rssBytes=($p|Measure-Object WorkingSet64 -Sum).Sum;privateBytes=($p|Measure-Object PrivateMemorySize64 -Sum).Sum;processCount=$p.Count}|ConvertTo-Json -Compress`;
      const { stdout } = await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { timeout: 10000, windowsHide: true });
      return { available: true, ...JSON.parse(stdout), accounting: 'Sum across browser processes; shared pages may be counted more than once' };
    }
    if (process.platform === 'linux') {
      let rssBytes = 0;
      for (const id of ids) {
        const status = await readFile(`/proc/${id}/status`, 'utf8');
        const match = status.match(/^VmRSS:\s+(\d+)\s+kB$/m);
        if (!match) throw new Error(`No RSS counter for PID ${id}`);
        rssBytes += Number(match[1]) * 1024;
      }
      return { available: true, rssBytes, processCount: ids.length,
        accounting: 'Sum of process RSS; shared pages may be counted more than once' };
    }
    return { available: false, reason: `Memory sampling unsupported on ${process.platform}` };
  } catch (error) { return { available: false, reason: error.message }; }
}

// This measures browser frame pacing, not GPU execution time.
async function workload(frameCount) {
  document.title = 'Thorium resource probe';
  document.body.style.cssText = 'margin:0;background:#14151a;color:#eee;font:16px sans-serif';
  const canvas = document.createElement('canvas');
  canvas.width = 640; canvas.height = 360;
  canvas.style.cssText = 'position:fixed;right:0;top:0;width:640px;height:360px;z-index:2';
  document.body.append(canvas);
  const gl = canvas.getContext('webgl2', { antialias: true });
  if (!gl) throw new Error('WebGL2 context unavailable');
  const shader = (type, source) => {
    const value = gl.createShader(type);
    if (!value) throw new Error('Shader allocation failed');
    gl.shaderSource(value, source); gl.compileShader(value);
    if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) {
      const error = gl.getShaderInfoLog(value); gl.deleteShader(value); throw new Error(error);
    }
    return value;
  };
  const vertex = shader(gl.VERTEX_SHADER, '#version 300 es\nvoid main(){vec2 p[3]=vec2[3](vec2(-0.8,-0.8),vec2(0.8,-0.8),vec2(0.0,0.8));gl_Position=vec4(p[gl_VertexID],0,1);}');
  const fragment = shader(gl.FRAGMENT_SHADER, '#version 300 es\nprecision highp float;uniform float t;out vec4 color;void main(){color=vec4(0.3+0.2*sin(t),0.5,0.9,1);}');
  const program = gl.createProgram();
  if (!program) throw new Error('Program allocation failed');
  gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const uniform = gl.getUniformLocation(program, 't');
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const webglRenderer = String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  const grid = document.createElement('main');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(6,1fr);gap:12px;padding:16px;max-width:1200px';
  const cards = [];
  for (let i = 0; i < 180; ++i) {
    const card = document.createElement('article');
    card.style.cssText = 'height:120px;border-radius:12px;background:linear-gradient(125deg,#25314c,#374163);padding:12px';
    card.textContent = `Tab ${i + 1} — render workload`;
    cards.push(card); grid.append(card);
  }
  document.body.append(grid);
  const samples = [];
  let previous;
  try {
    for (let frame = 0; frame < frameCount + 30; ++frame) {
      const now = await new Promise(requestAnimationFrame);
      if (document.hidden) throw new Error('Probe window became hidden; foreground frame measurement invalid');
      if (frame >= 30 && previous !== undefined) samples.push(now - previous);
      previous = now;
      window.scrollTo(0, (frame * 9) % 2000);
      for (let i = 0; i < cards.length; ++i) cards[i].style.transform = `translateX(${Math.sin(frame / 15 + i) * 3}px)`;
      gl.clearColor(0.05, 0.06, 0.1, 1); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform1f(uniform, now / 1000); gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (gl.isContextLost()) throw new Error('GPU context lost during workload');
    }
    return { samples, webglRenderer, viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      cards: cards.length, canvas: { width: canvas.width, height: canvas.height }, warmupFrames: 30 };
  } finally {
    gl.deleteProgram(program); gl.deleteShader(vertex); gl.deleteShader(fragment);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    grid.remove(); canvas.remove();
  }
}

async function runProbe(executable, options) {
  const profile = await mkdtemp(join(tmpdir(), 'thorium-gpu-probe-'));
  const warnings = [];
  let client, child, result, failure;
  let stderr = '';
  try {
    const start = performance.now();
    child = spawn(executable, ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', '--window-size=1280,800',
      ...(options.headless ? ['--headless=new'] : []), 'about:blank'],
    { stdio: ['ignore', 'ignore', 'pipe'], detached: process.platform !== 'win32' });
    let launchError;
    child.on('error', error => { launchError = error; });
    child.stderr.on('data', data => { stderr = (stderr + data.toString()).slice(-32768); });
    let endpoint;
    const deadline = Date.now() + 30000;
    while (!endpoint && Date.now() < deadline) {
      if (launchError) throw launchError;
      if (child.exitCode !== null) throw new Error(`Browser exited (${child.exitCode}): ${stderr}`);
      try {
        const [port, path] = (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);
        if (/^\d+$/.test(port) && Number(port) > 0 && Number(port) <= 65535 && path?.startsWith('/devtools/browser/')) {
          endpoint = `ws://127.0.0.1:${port}${path}`;
        }
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (!endpoint) await delay(100);
    }
    if (!endpoint) throw new Error(`Browser CDP startup timed out: ${stderr}`);
    client = await CdpClient.connect(endpoint);
    const startupMs = performance.now() - start;
    const version = await client.call('Browser.getVersion');
    const { gpu } = await client.call('SystemInfo.getInfo');
    const classification = classifyGpu(gpu);
    const { sessionId } = await waitForPage(client);
    const before = await client.call('SystemInfo.getProcessInfo');
    const memoryBefore = await processMemory(before.processInfo.map(item => item.id));
    const memoryDuring = [];
    let sampling = true;
    const sampler = (async () => {
      while (sampling) {
        await delay(500);
        if (!sampling) break;
        try {
          const processes = await client.call('SystemInfo.getProcessInfo');
          memoryDuring.push({ elapsedMs: performance.now() - start,
            ...await processMemory(processes.processInfo.map(item => item.id)) });
        } catch (error) { warnings.push(`Memory sampling stopped: ${error.message}`); break; }
      }
    })();
    let evaluation;
    try {
      evaluation = await client.call('Runtime.evaluate', {
        expression: `(${workload.toString()})(${options.frames})`, awaitPromise: true, returnByValue: true,
      }, sessionId, 45000);
    } finally { sampling = false; await sampler; }
    if (evaluation.exceptionDetails) throw new Error(evaluation.exceptionDetails.exception?.description ?? evaluation.exceptionDetails.text);
    const measurement = evaluation.result.value;
    const after = await client.call('SystemInfo.getProcessInfo');
    const memoryAfter = await processMemory(after.processInfo.map(item => item.id));
    const cpuSeconds = list => list.reduce((sum, item) => sum + item.cpuTime, 0);
    const pageSoftware = /swiftshader|llvmpipe|softpipe|software|basic render|\bwarp\b/i.test(measurement.webglRenderer);
    result = { timestamp: new Date().toISOString(), executable, platform: process.platform,
      browser: version, startupToCdpMs: startupMs, headed: !options.headless,
      gpu: { classification, devices: gpu.devices, featureStatus: gpu.featureStatus, auxAttributes: gpu.auxAttributes },
      hardwarePerformanceEligible: classification.hardware && !pageSoftware && !options.headless,
      workload: { ...measurement, samples: undefined, frames: frameSummary(measurement.samples),
        metric: 'End-to-end foreground frame intervals, not GPU timer-query execution time' },
      memoryBefore, memoryDuring, memoryAfterTeardown: memoryAfter,
      peakSampledRssBytes: memoryDuring.filter(sample => sample.available).reduce((peak, sample) => Math.max(peak, sample.rssBytes), 0) || null,
      processCpuSeconds: Math.max(0, cpuSeconds(after.processInfo) - cpuSeconds(before.processInfo)),
      limitations: ['Clean temporary profile only; no real-world tabs or extensions',
        'Startup includes OS cache effects; repeat runs under the same conditions',
        'Whole-browser process memory may include shared pages more than once',
        'During-workload memory samples every 500 ms are a lower bound on peak usage and add measurement overhead',
        'One run does not establish a speedup over another browser'], cleanupWarnings: warnings };
  } catch (error) { failure = error; }
  finally {
    if (client) {
      try { await client.call('Browser.close', {}, undefined, 3000); }
      catch (error) { warnings.push(`Browser.close: ${error.message}`); }
      client.close();
    }
    if (child?.pid) {
      for (let i = 0; i < 20 && child.exitCode === null; ++i) await delay(100);
      if (child.exitCode === null || process.platform !== 'win32') {
        try {
          if (process.platform === 'win32') await execute('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { timeout: 5000, windowsHide: true });
          else process.kill(-child.pid, 'SIGKILL');
        } catch (error) { if (error.code !== 'ESRCH') warnings.push(`Process cleanup: ${error.message}`); }
        for (let i = 0; i < 20 && child.exitCode === null; ++i) await delay(100);
      } else if (failure) {
        warnings.push('Browser parent exited early; Windows descendant cleanup cannot be verified');
      }
    }
    try { await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); }
    catch (error) { warnings.push(`Temporary profile cleanup: ${error.message}`); }
  }
  if (failure) throw new Error(`${failure.message}${warnings.length ? `; ${warnings.join('; ')}` : ''}`);
  return result;
}

async function main(args) {
  if (args.includes('--help') || args.length === 0) {
    console.log('Usage: node tools/gpu_probe.mjs --browser C:/path/thorium.exe --output gpu-report.json [--frames 180] [--headless] [--allow-software]');
    return;
  }
  const options = { frames: 180, headless: false, allowSoftware: false };
  let browser, output;
  for (let i = 0; i < args.length; ++i) {
    if (args[i] === '--browser') browser = args[++i];
    else if (args[i] === '--output') output = args[++i];
    else if (args[i] === '--frames') options.frames = Number(args[++i]);
    else if (args[i] === '--headless') options.headless = true;
    else if (args[i] === '--allow-software') options.allowSoftware = true;
    else throw new Error(`Unknown argument: ${args[i]}`);
  }
  if (!browser || !output || !Number.isInteger(options.frames) || options.frames < 60 || options.frames > 1200) {
    throw new Error('--browser, --output and --frames between 60 and 1200 are required');
  }
  const report = await runProbe(resolve(browser), options);
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(`Report: ${resolve(output)}; hardware eligible: ${report.hardwarePerformanceEligible}`);
  if (!report.hardwarePerformanceEligible && !options.allowSoftware) {
    throw new Error('Report saved, but hardware foreground rendering was not confirmed; do not use it as hardware performance evidence');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
}
