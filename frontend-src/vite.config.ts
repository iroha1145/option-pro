import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // 本地 live 调试:/api 反代到生产(只读验证用)
      // headers.origin 改写为目标源:后端 require_same_origin_* 校验 Origin==Host,
      // 否则 dev 下所有写操作(batch/登录/触发)都会被如实拒绝
      "/api": {
        target: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
        changeOrigin: true,
        secure: true,
        headers: {
          origin: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
