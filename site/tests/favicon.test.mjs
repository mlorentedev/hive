import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

for (const page of ['index.html', 'es/index.html']) {
	test(`${page} links the PNG favicon under the site base`, () => {
		const html = readFileSync(new URL(`../dist/${page}`, import.meta.url), 'utf8');
		assert.ok(
			/<link rel="shortcut icon" href="\/hive\/favicon\.png" type="image\/png"\/>/.test(html),
			`PNG favicon missing from ${page}`,
		);
		const svg = html.match(
			/<link(?=[^>]*\brel="icon")(?=[^>]*\btype="image\/svg\+xml")(?=[^>]*\bhref="\/hive\/favicon\.svg")[^>]*>/,
		);
		assert.ok(svg, `SVG favicon missing from ${page}`);
	});
}

test('the published favicon is a 32px PNG', () => {
	const png = readFileSync(new URL('../dist/favicon.png', import.meta.url));
	assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
	assert.equal(png.readUInt32BE(16), 32);
	assert.equal(png.readUInt32BE(20), 32);
});
