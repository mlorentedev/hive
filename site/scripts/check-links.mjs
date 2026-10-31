// Fails the build when a page links to a root-relative URL that misses the
// site base or points at a page that was not built (#519). Starlight does not
// rewrite absolute Markdown links, so `](/configuration/)` ships as-is and
// 404s on GitHub Pages, where the site lives under /hive/.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const BASE = '/hive/';
// The dist directory is an argument so the tests can point it at a fixture.
const DIST = resolve(process.argv[2] ?? fileURLToPath(new URL('../dist/', import.meta.url)));

function* htmlFiles(dir) {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) yield* htmlFiles(path);
		else if (entry.name.endsWith('.html')) yield path;
	}
}

// A directory is not a page: /hive/guides/ with built children but no
// guides/index.html still 404s.
function isFile(path) {
	return statSync(path, { throwIfNoEntry: false })?.isFile() ?? false;
}

function targetExists(url) {
	const path = decodeURIComponent(url.slice(BASE.length).split(/[?#]/)[0]);
	return isFile(join(DIST, path, 'index.html')) || isFile(join(DIST, path));
}

const problems = [];
for (const file of htmlFiles(DIST)) {
	const html = readFileSync(file, 'utf8');
	const page = file.slice(DIST.length + 1);
	// Either quote, optional spaces around `=`; `//host` is protocol-relative, not internal.
	for (const [, , url] of html.matchAll(/\b(?:href|src)\s*=\s*(["'])(\/(?!\/)[^"']*)\1/g)) {
		if (!url.startsWith(BASE)) problems.push(`${page}: ${url} (missing ${BASE} base)`);
		else if (!targetExists(url)) problems.push(`${page}: ${url} (no such page)`);
	}
}

if (problems.length) {
	console.error(`${problems.length} broken internal link(s):\n${problems.join('\n')}`);
	process.exit(1);
}
console.log('internal links OK');
