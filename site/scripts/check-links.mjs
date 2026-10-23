// Fails the build when a page links to a root-relative URL that misses the
// site base or points at a page that was not built (#519). Starlight does not
// rewrite absolute Markdown links, so `](/configuration/)` ships as-is and
// 404s on GitHub Pages, where the site lives under /hive/.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const BASE = '/hive/';
const DIST = new URL('../dist/', import.meta.url).pathname;

function* htmlFiles(dir) {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) yield* htmlFiles(path);
		else if (entry.name.endsWith('.html')) yield path;
	}
}

function targetExists(url) {
	const path = decodeURIComponent(url.slice(BASE.length).split(/[?#]/)[0]);
	return existsSync(join(DIST, path, 'index.html')) || existsSync(join(DIST, path));
}

const problems = [];
for (const file of htmlFiles(DIST)) {
	const html = readFileSync(file, 'utf8');
	for (const [, url] of html.matchAll(/(?:href|src)="(\/[^/"][^"]*|\/)"/g)) {
		if (!url.startsWith(BASE)) problems.push(`${file.slice(DIST.length)}: ${url} (missing ${BASE} base)`);
		else if (!targetExists(url)) problems.push(`${file.slice(DIST.length)}: ${url} (no such page)`);
	}
}

if (problems.length) {
	console.error(`${problems.length} broken internal link(s):\n${problems.join('\n')}`);
	process.exit(1);
}
console.log('internal links OK');
