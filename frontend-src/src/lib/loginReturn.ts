/**
 * 去登录时带上出发点，登录成功后回到那里（路由状态 `{ from }`）。
 * 出发点只收站内路径：必须恰好以一个「/」开头。
 */

/**
 * 「//host」会被当成别的站点；「/\host」同样，浏览器会先把反斜杠改写成斜杠。
 * react-router 对这类目标直接抛「External navigation is not allowed」，登录成功后整页变成错误页。
 * 带协议的地址、相对写法和非字符串一概不收。
 */
export function safeReturnPath(value: unknown): string | null {
  return typeof value === 'string' && /^\/(?![\\/])/.test(value) ? value : null;
}

/** 每个「去登录」入口共用：出发点取当前页的路径、查询串和锚点；登录页自己不能当出发点。 */
export function loginReturnState(location: { pathname: string; search: string; hash: string }): { from: string } | undefined {
  const from = safeReturnPath(location.pathname + location.search + location.hash);
  return from && !/^\/login(?:[/?#]|$)/.test(from) ? { from } : undefined;
}
