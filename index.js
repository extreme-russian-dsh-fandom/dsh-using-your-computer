/**
 * dsh-computer-use — drive the user's Windows desktop from the agent.
 *
 * The skill in ./computer-use/ tells the agent HOW to use the engine; this
 * plugin is what gives the agent a TOOL to reach it. One `computer_use` tool
 * runs the Python engine and returns its stdout, so the whole command surface
 * (begin, screenshot, click, type, key, config, ...) is one call away.
 *
 * Why a subprocess and not a native port: the engine is dependency-free Python
 * using ctypes against Win32, which is exactly what makes it small and portable
 * here. Re-implementing SendInput, layered windows and a low-level keyboard
 * hook in Node would be a second, untested copy of the hardest code in the
 * project.
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'computer-use';
export const inject = ['tools'];

const HERE = dirname(fileURLToPath(import.meta.url));

const DEFAULTS = {
  pythonPath: '',
  scriptPath: '',
  timeoutMs: 120_000,
};

/**
 * Resolve the engine script and the Python that runs it.
 *
 * Both are discoverable rather than hard-coded so the plugin keeps working when
 * DSH moves its bundled runtime or the user runs a different Python.
 */
export function resolveConfig(raw) {
  const source = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const config = { ...DEFAULTS, ...source };

  if (typeof config.pythonPath !== 'string') throw new Error('computer-use: pythonPath must be a string.');
  if (typeof config.scriptPath !== 'string') throw new Error('computer-use: scriptPath must be a string.');
  if (!Number.isInteger(config.timeoutMs) || config.timeoutMs < 5_000 || config.timeoutMs > 1_800_000) {
    throw new Error('computer-use: timeoutMs must be an integer between 5000 and 1800000.');
  }

  const scriptPath = config.scriptPath || join(HERE, 'computer-use', 'scripts', 'computer_use.py');
  const pythonPath = config.pythonPath || process.env.COMPUTER_USE_PYTHON || 'python';

  return { ...config, scriptPath, pythonPath };
}

/**
 * Report what the engine sees, without touching the input devices.
 *
 * @param {object} resolved - resolved plugin config.
 * @param {AbortSignal} signal - the call's cancellation signal.
 */
function runEngine(resolved, args, signal, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(resolved.pythonPath, [resolved.scriptPath, ...args], {
      windowsHide: true,
      signal,
    });

    let stdout = '';
    let stderr = '';
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error(`computer_use ${args.join(' ')} exceeded ${timeoutMs} ms and was stopped.`));
    }, timeoutMs);

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });

    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(
        `Could not start the computer-use engine (${resolved.pythonPath}). `
        + `Set pythonPath in the plugin config or COMPUTER_USE_PYTHON. Cause: ${error.message}`,
      ));
    });

    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, stdout: stdout.trim(), stderr: stderr.trim() });
    });
  });
}

function text(value) {
  return [{ type: 'text', text: value }];
}

function renderResult(value) {
  const parts = [`exit ${value.code}`];
  if (value.stdout) parts.push(value.stdout);
  if (value.stderr) parts.push(`stderr:\n${value.stderr}`);
  return parts.join('\n');
}

export function apply(ctx, config) {
  const resolved = resolveConfig(config);

  ctx.tools.register({
    name: 'computer_use',
    description: [
      'Control the user\'s Windows desktop: screenshot, move the mouse, click, drag, scroll, type, press keys.',
      'Always call with args ["begin"] first and ["end"] last.',
      'The user can cancel at any time by pressing the cancel key shown on the island (ESC by default).',
      'Actions: begin [--cancel-key SPEC], end, status, config [--cancel-key SPEC] [--reset],',
      'screenshot [--out path], cursor_position, move X Y, click X Y [--button left|right|middle] [--clicks N],',
      'drag X1 Y1 X2 Y2, scroll X Y AMOUNT [--horizontal], type --text "..." | --file path, key "ctrl+c",',
      'highlight X Y, wait --stable [--timeout ms], self_test.',
      'Coordinates are physical pixels and match the screenshot exactly.',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        args: {
          type: 'array',
          items: { type: 'string' },
          description: 'Engine arguments, e.g. ["click","812","540"] or ["begin"].',
        },
      },
      required: ['args'],
    },
    output: {
      schema: { type: 'object' },
      render: (_args, value) => text(renderResult(value)),
    },
    timeoutMs: resolved.timeoutMs + 5_000,
    // Every action touches the real cursor and keyboard, so two calls in one
    // turn must not interleave.
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      if (!Array.isArray(args.args) || args.args.length === 0) {
        throw new Error('computer_use needs a non-empty args array, e.g. ["begin"].');
      }
      if (!existsSync(resolved.scriptPath)) {
        throw new Error(
          `The computer-use engine is missing at ${resolved.scriptPath}. `
          + 'Reinstall the plugin, or set scriptPath in the plugin config.',
        );
      }
      return runEngine(resolved, args.args.map(String), exec.signal, resolved.timeoutMs);
    },
    presentCall: (args) => ({
      card: 'generic',
      title: `Computer use: ${args.args.slice(0, 2).join(' ')}`,
      kind: 'other',
    }),
  });
}
