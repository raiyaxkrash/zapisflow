import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';

describe('ZapisFlow Marketing Responsive & Production Verification', () => {
  const rootDir = process.cwd();
  const distHtmlPath = resolve(rootDir, 'dist/index.html');
  const distRobotsPath = resolve(rootDir, 'dist/robots.txt');
  const distSitemapPath = resolve(rootDir, 'dist/sitemap.xml');
  const distFaviconPath = resolve(rootDir, 'dist/favicon.svg');
  const distOgPath = resolve(rootDir, 'dist/og-image.svg');
  const cssPath = resolve(rootDir, 'src/index.css');

  it('generates production static artifacts in dist/', () => {
    expect(existsSync(distHtmlPath)).toBe(true);
    expect(existsSync(distRobotsPath)).toBe(true);
    expect(existsSync(distSitemapPath)).toBe(true);
    expect(existsSync(distFaviconPath)).toBe(true);
    expect(existsSync(distOgPath)).toBe(true);
  });

  it('includes proper mobile viewport and SEO meta tags in dist/index.html', () => {
    const html = readFileSync(distHtmlPath, 'utf-8');
    expect(html).toContain('name="viewport"');
    expect(html).toContain('width=device-width');
    expect(html).toContain('rel="canonical"');
    expect(html).toContain('property="og:title"');
    expect(html).toContain('property="og:image"');
    expect(html).toContain('name="twitter:card"');
    expect(html).toContain('application/ld+json');
  });

  it('contains responsive media queries for target device breakpoints (375px, 390px, 640px, 768px, 900px, 1024px)', () => {
    const css = readFileSync(cssPath, 'utf-8');
    expect(css).toContain('@media (max-width: 390px)');
    expect(css).toContain('@media (min-width: 640px)');
    expect(css).toContain('@media (min-width: 768px)');
    expect(css).toContain('@media (min-width: 900px)');
    expect(css).toContain('@media (min-width: 1024px)');
  });

  it('protects against horizontal overflow and respects user motion settings', () => {
    const css = readFileSync(cssPath, 'utf-8');
    expect(css).toContain('overflow-x: hidden');
    expect(css).toContain('box-sizing: border-box');
    expect(css).toContain('prefers-reduced-motion: reduce');
    expect(css).toContain(':focus-visible');
  });

  it('supports light and dark theme design tokens', () => {
    const css = readFileSync(cssPath, 'utf-8');
    expect(css).toContain(':root');
    expect(css).toContain('[data-theme="dark"]');
    expect(css).toContain('--bg-primary');
    expect(css).toContain('--text-primary');
    expect(css).toContain('--primary');
  });
});
