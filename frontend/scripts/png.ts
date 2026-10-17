import { deflateSync, inflateSync } from 'node:zlib'

/*
 * Shrinks README screenshots without an image dependency: decodes the 8-bit RGB(A) PNG a
 * browser produces, reduces it to an indexed palette (exact when the image has at most 256
 * colours, median cut otherwise), and re-encodes it with maximum zlib compression. Flat UI
 * colours survive; anti-aliased text edges are approximated by the nearest palette colour.
 */

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

interface Image {
  width: number
  height: number
  /** One 0xRRGGBB value per pixel. */
  pixels: Uint32Array
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c
  const pa = Math.abs(p - a)
  const pb = Math.abs(p - b)
  const pc = Math.abs(p - c)
  if (pa <= pb && pa <= pc) return a
  return pb <= pc ? b : c
}

export function decodePng(png: Buffer): Image {
  if (!png.subarray(0, 8).equals(SIGNATURE)) throw new Error('not a PNG file')
  let offset = 8
  let width = 0
  let height = 0
  let channels = 0
  const data: Buffer[] = []
  while (offset < png.length) {
    const length = png.readUInt32BE(offset)
    const type = png.toString('latin1', offset + 4, offset + 8)
    const body = png.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      width = body.readUInt32BE(0)
      height = body.readUInt32BE(4)
      const [depth, colorType, , , interlace] = [body[8], body[9], body[10], body[11], body[12]]
      if (depth !== 8 || interlace !== 0 || (colorType !== 2 && colorType !== 6)) {
        throw new Error(`unsupported PNG (bit depth ${depth}, colour type ${colorType}, interlace ${interlace})`)
      }
      channels = colorType === 6 ? 4 : 3
    } else if (type === 'IDAT') {
      data.push(body)
    } else if (type === 'IEND') {
      break
    }
    offset += 12 + length
  }
  const raw = inflateSync(Buffer.concat(data))
  const stride = width * channels
  const pixels = new Uint32Array(width * height)
  let previous = new Uint8Array(stride)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1))
    const current = new Uint8Array(stride)
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? current[x - channels]! : 0
      const up = previous[x]!
      const upLeft = x >= channels ? previous[x - channels]! : 0
      const value = line[x]!
      switch (filter) {
        case 0:
          current[x] = value
          break
        case 1:
          current[x] = value + left
          break
        case 2:
          current[x] = value + up
          break
        case 3:
          current[x] = value + ((left + up) >> 1)
          break
        case 4:
          current[x] = value + paeth(left, up, upLeft)
          break
        default:
          throw new Error(`invalid PNG filter ${filter}`)
      }
    }
    for (let x = 0; x < width; x++) {
      const i = x * channels
      if (channels === 4 && current[i + 3] !== 255) throw new Error('transparent PNGs are not supported')
      pixels[y * width + x] = (current[i]! << 16) | (current[i + 1]! << 8) | current[i + 2]!
    }
    previous = current
  }
  return { width, height, pixels }
}

const channel = (color: number, shift: number) => (color >> shift) & 0xff

interface Box {
  colors: number[]
  counts: number[]
  population: number
}

function makeBox(colors: number[], histogram: Map<number, number>): Box {
  const counts = colors.map((c) => histogram.get(c)!)
  return { colors, counts, population: counts.reduce((a, b) => a + b, 0) }
}

/** The channel (as a bit shift) with the widest spread in the box, and that spread. */
function widest(box: Box): [number, number] {
  let best: [number, number] = [16, -1]
  for (const shift of [16, 8, 0]) {
    let min = 255
    let max = 0
    for (const c of box.colors) {
      const v = channel(c, shift)
      if (v < min) min = v
      if (v > max) max = v
    }
    if (max - min > best[1]) best = [shift, max - min]
  }
  return best
}

/** Median cut over the colour histogram: at most `size` representative colours. */
function medianCut(histogram: Map<number, number>, size: number): number[] {
  const boxes = [makeBox([...histogram.keys()], histogram)]
  while (boxes.length < size) {
    // Split the box whose pixels are spread the most (population x widest channel range).
    let pick = -1
    let score = 0
    for (let i = 0; i < boxes.length; i++) {
      const box = boxes[i]!
      if (box.colors.length < 2) continue
      const s = box.population * widest(box)[1]
      if (s > score) [pick, score] = [i, s]
    }
    if (pick < 0) break
    const box = boxes[pick]!
    const [shift] = widest(box)
    const sorted = [...box.colors].sort((a, b) => channel(a, shift) - channel(b, shift))
    let seen = 0
    let cut = 1
    for (; cut < sorted.length - 1; cut++) {
      seen += histogram.get(sorted[cut - 1]!)!
      if (seen * 2 >= box.population) break
    }
    boxes.splice(pick, 1, makeBox(sorted.slice(0, cut), histogram), makeBox(sorted.slice(cut), histogram))
  }
  return boxes.map((box) => {
    const sum = [0, 0, 0]
    box.colors.forEach((c, i) => {
      const n = box.counts[i]!
      sum[0]! += channel(c, 16) * n
      sum[1]! += channel(c, 8) * n
      sum[2]! += channel(c, 0) * n
    })
    const [r, g, b] = sum.map((s) => Math.round(s / box.population)) as [number, number, number]
    return (r << 16) | (g << 8) | b
  })
}

function nearest(color: number, palette: number[]): number {
  let best = 0
  let distance = Infinity
  for (let i = 0; i < palette.length; i++) {
    const p = palette[i]!
    const dr = channel(color, 16) - channel(p, 16)
    const dg = channel(color, 8) - channel(p, 8)
    const db = channel(color, 0) - channel(p, 0)
    // Weighted towards green, as the eye is (cheap perceptual approximation).
    const d = 2 * dr * dr + 4 * dg * dg + 3 * db * db
    if (d < distance) [best, distance] = [i, d]
  }
  return best
}

function chunk(type: string, body: Buffer): Buffer {
  const head = Buffer.alloc(8)
  head.writeUInt32BE(body.length, 0)
  head.write(type, 4, 'latin1')
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), body])), 0)
  return Buffer.concat([head, body, crc])
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c >>> 0
})

function crc32(bytes: Buffer): number {
  let c = 0xffffffff
  for (const byte of bytes) c = CRC_TABLE[(c ^ byte) & 0xff]! ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

export function encodeIndexed(image: Image, palette: number[], indexes: Uint8Array): Buffer {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(image.width, 0)
  header.writeUInt32BE(image.height, 4)
  header[8] = 8 // bit depth
  header[9] = 3 // indexed colour
  const plte = Buffer.alloc(palette.length * 3)
  palette.forEach((c, i) => {
    plte[i * 3] = channel(c, 16)
    plte[i * 3 + 1] = channel(c, 8)
    plte[i * 3 + 2] = channel(c, 0)
  })
  // Filter type 0 on every line: the usual best choice for palette images.
  const raw = Buffer.alloc(image.height * (image.width + 1))
  for (let y = 0; y < image.height; y++) {
    raw.set(indexes.subarray(y * image.width, (y + 1) * image.width), y * (image.width + 1) + 1)
  }
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', header),
    chunk('PLTE', plte),
    chunk('IDAT', deflateSync(raw, { level: 9, memLevel: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** The screenshot as an indexed PNG of at most `colors` colours, or the input if smaller. */
export function optimizePng(png: Buffer, colors = 256): Buffer {
  const image = decodePng(png)
  const histogram = new Map<number, number>()
  for (const p of image.pixels) histogram.set(p, (histogram.get(p) ?? 0) + 1)
  const palette = histogram.size <= colors ? [...histogram.keys()] : medianCut(histogram, colors)
  const slot = new Map<number, number>()
  for (const color of histogram.keys()) slot.set(color, nearest(color, palette))
  const indexes = new Uint8Array(image.pixels.length)
  image.pixels.forEach((p, i) => (indexes[i] = slot.get(p)!))
  const optimized = encodeIndexed(image, palette, indexes)
  return optimized.length < png.length ? optimized : png
}
