import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { parse } from 'yaml';
import { apply, resolveConfig } from '../index.js';

const source = readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8');
function patchConfig(env = {}, yaml = source) {
  return parse(yaml, { customTags: [{ tag: 'tag:yaml.org,2002:js', resolve: value =>
    vm.runInNewContext(value, { process: { env } }) }] })[0].insert[0].config;
}

test('bundle defaults activate the plugin and locate its packaged engine', () => {
  let tool;
  apply({ tools: { register(value) { tool = value; } } }, patchConfig());
  assert.equal(tool.name, 'computer_use');
  assert.equal(tool.timeoutMs, 125000);
  assert.ok(readFileSync(resolveConfig(patchConfig()).scriptPath).length);
});

test('environment timeout and Python overrides survive YAML parsing', () => {
  const config = resolveConfig(patchConfig({ COMPUTER_USE_TIMEOUT_MS: '6000', COMPUTER_USE_PYTHON: 'custom-python' }));
  assert.equal(config.timeoutMs, 6000);
  assert.equal(config.pythonPath, 'custom-python');
});

test('valid timeout boundaries and numeric strings', () => {
  for (const value of [5000, 120000, 1800000, '5000', ' 120000 ']) {
    assert.equal(resolveConfig({ timeoutMs: value }).timeoutMs, Number(value));
  }
});

test('invalid timeout values fail before tool registration', () => {
  for (const value of [4999, 1800001, 5.5, null, {}, [], true, '', 'abc', Infinity, NaN]) {
    assert.throws(() => apply({ tools: { register() { assert.fail('registered invalid config'); } } }, { timeoutMs: value }), /timeoutMs/);
  }
});

test('old unquoted YAML reproduces activation failure', () => {
  const broken = source.replace(/timeoutMs: !!js '([^\n]+)'/, 'timeoutMs: !!js $1');
  assert.throws(() => resolveConfig(patchConfig({}, broken)), /timeoutMs/);
});
