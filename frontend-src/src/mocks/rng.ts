/** 确定性种子随机（design.md §11：保证每次构建一致） */

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class Rng {
  private next: () => number;
  constructor(seed: number) {
    this.next = mulberry32(seed);
  }
  float(min = 0, max = 1): number {
    return min + this.next() * (max - min);
  }
  int(min: number, max: number): number {
    return Math.floor(this.float(min, max + 1));
  }
  pick<T>(arr: readonly T[]): T {
    return arr[Math.floor(this.next() * arr.length)];
  }
  /** 近似正态（中心极限，clamp 到 [min,max]） */
  normal(mean: number, sd: number, min: number, max: number): number {
    let s = 0;
    for (let i = 0; i < 6; i++) s += this.next();
    const z = (s - 3) / 1.225; // ≈ N(0,1)
    return Math.min(max, Math.max(min, mean + z * sd));
  }
  chance(p: number): boolean {
    return this.next() < p;
  }
}

export function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
export function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}
