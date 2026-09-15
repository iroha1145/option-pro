import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve('test-results/eps-chart-build');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  const relative = path.extname(pathname) ? pathname : '/index.html';
  const file = path.resolve(root, `.${relative}`);
  if (!file.startsWith(`${root}${path.sep}`)) { response.writeHead(403); response.end(); return; }
  fs.readFile(file, (error, data) => {
    if (error) { response.writeHead(404); response.end(); return; }
    response.setHeader('Content-Type', types[path.extname(file)] ?? 'application/octet-stream');
    response.setHeader('Cache-Control', 'no-store');
    response.end(data);
  });
}).listen(3027, '127.0.0.1');
