// 取证截图的唯一落点。四个 spec 各自抄一份 SCREENSHOT_DIR + mkdir 的后果是
// 改目录要改四处，而且抄件已经分头漂移（同步 mkdirSync vs 异步 mkdir）。
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

export const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");

/** 截一张取证图；目录按需创建。locator 省略时截整个视口。 */
export async function captureEvidence(target, name, options = {}) {
  await mkdir(SCREENSHOT_DIR, { recursive: true });
  return target.screenshot({ path: join(SCREENSHOT_DIR, `${name}.png`), ...options });
}
