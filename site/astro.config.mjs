// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
	site: 'https://mlorentedev.github.io',
	base: '/hive',
	integrations: [
		starlight({
			title: 'Hive',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/mlorentedev/hive' }],
			sidebar: [
				{ label: 'Getting Started', slug: 'getting-started' },
				{ label: 'Configuration', slug: 'configuration' },
				{
					label: 'Tools',
					items: [
						{ label: 'Vault Tools', slug: 'tools/vault' },
						{ label: 'Worker Tools', slug: 'tools/worker' },
					],
				},
				{
					label: 'Guides',
					items: [
						{ label: 'Use Cases', slug: 'guides/use-cases' },
						{ label: 'Vault Structure', slug: 'guides/vault-structure' },
						{ label: 'Worker Routing', slug: 'guides/worker-routing' },
						{ label: 'Prompts', slug: 'guides/prompts' },
					],
				},
				{
					label: 'Reference',
					items: [
						{ label: 'Resources', slug: 'reference/resources' },
						{ label: 'Architecture', slug: 'reference/architecture' },
					],
				},
			],
		}),
	],
});
