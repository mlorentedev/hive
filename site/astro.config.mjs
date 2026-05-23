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
			customCss: ['./src/styles/custom.css'],
			defaultLocale: 'root',
			locales: {
				root: {
					label: 'EN',
					lang: 'en',
				},
				es: {
					label: 'ES',
					lang: 'es',
				},
			},
			head: [
				{ tag: 'meta', attrs: { name: 'theme-color', content: '#0e7490' } },
				{
					tag: 'script',
					attrs: { type: 'application/ld+json' },
					content: JSON.stringify({
						'@context': 'https://schema.org',
						'@type': 'SoftwareApplication',
						name: 'Hive',
						description: 'MCP server for on-demand Obsidian vault access for AI coding assistants',
						applicationCategory: 'DeveloperApplication',
						operatingSystem: 'Cross-platform',
						url: 'https://github.com/mlorentedev/hive',
						offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
						license: 'https://opensource.org/licenses/MIT',
					}),
				},
			],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/mlorentedev/hive' }],
			sidebar: [
				{
					label: 'Getting Started',
					translations: { es: 'Primeros Pasos' },
					slug: 'getting-started',
				},
				{
					label: 'Configuration',
					translations: { es: 'Configuración' },
					slug: 'configuration',
				},
				{
					label: 'Tools',
					translations: { es: 'Herramientas' },
					items: [
						{ label: 'Vault Tools', translations: { es: 'Herramientas de Vault' }, slug: 'tools/vault' },
						{ label: 'Worker Tools', translations: { es: 'Herramientas de Worker' }, slug: 'tools/worker' },
					],
				},
				{
					label: 'Guides',
					translations: { es: 'Guías' },
					items: [
						{ label: 'Use Cases', translations: { es: 'Casos de Uso' }, slug: 'guides/use-cases' },
						{ label: 'Vault Structure', translations: { es: 'Estructura del Vault' }, slug: 'guides/vault-structure' },
						{ label: 'Worker Routing', translations: { es: 'Enrutamiento de Workers' }, slug: 'guides/worker-routing' },
						{ label: 'Prompts', slug: 'guides/prompts' },
						{ label: 'Benchmarks', slug: 'guides/benchmarks' },
						{ label: 'Troubleshooting', translations: { es: 'Solución de Problemas' }, slug: 'guides/troubleshooting' },
						{ label: 'Obsidian-Git Integration', translations: { es: 'Integración con Obsidian-Git' }, slug: 'guides/obsidian-git-integration' },
					],
				},
				{
					label: 'Reference',
					translations: { es: 'Referencia' },
					items: [
						{ label: 'Resources', translations: { es: 'Recursos' }, slug: 'reference/resources' },
						{ label: 'Architecture', translations: { es: 'Arquitectura' }, slug: 'reference/architecture' },
					],
				},
			],
		}),
	],
});
