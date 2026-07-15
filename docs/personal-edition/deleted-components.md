# 已删除组件

## PR 1：旧版前端

| 删除范围 | 原用途 | 替代路径 | 引用证据 | 回滚提交 |
|---|---|---|---|---|
| `frontend/static/css/styles.css`、八个 `optix-*-v3.css` 与 `optix-nightday-v4.css` | 旧版页面及第三、四版样式 | `optix-deck.css`、`optix-catalysts.css` | `frontend/index.html` 在删除前只加载夜间工作台样式；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| `frontend/static/js/app.js`、`api.js`、`pages/`、`components/`、`utils/` | 旧路由、旧页面及旧组件树 | `deck-app.js`、`deck-api.js`、`deck-ai-jobs.js`、`deck-catalysts.js` | 首页无旧脚本标签；后端完整性清单和静态断言已改为现行九个文件；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| `frontend/static/js/icons.js`、`theme-toggle.js`、`frontend/static/icons.svg` | 旧图标和主题切换入口 | 夜间工作台内建图标和 `theme-init.js` | 首页无引用；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| 九个仅覆盖旧页面的 Node.js 测试 | 验证已下线页面状态 | `catalyst-deck.test.mjs` 与重写后的 `static_assertions.mjs` | 被测模块已删除；现行 Node.js 测试 32 项通过 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |

删除前逐项核对了首页脚本、静态导入、后端挂载、容器复制、文档与测试引用。删除后再次扫描旧路径，没有发现生产引用。恢复时应整体回到上述提交，不应单独复制旧资源回现行镜像。
