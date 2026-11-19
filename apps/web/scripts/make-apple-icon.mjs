// Makes src/app/apple-icon.png (the iPhone home-screen icon) from src/app/icon.svg.
// iOS shows transparent pixels as black, so the arcs go on white, with a margin
// so iOS's rounded corners never clip them. Run: `npm run icons`.
// sharp comes with Next (it resizes images); the PNG is committed, so builds never run this.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const app = new URL('../src/app/', import.meta.url)
const svg = readFileSync(new URL('icon.svg', app), 'utf8')
  // Widen the 24-unit grid by 4 units on each side: the margin.
  .replace('viewBox="0 0 24 24"', 'viewBox="-4 -4 32 32" width="180" height="180"')

await sharp(Buffer.from(svg))
  .flatten({ background: '#ffffff' })
  .removeAlpha()
  .png()
  .toFile(fileURLToPath(new URL('apple-icon.png', app)))
