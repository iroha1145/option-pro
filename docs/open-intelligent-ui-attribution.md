# OpenIntelligentUI 来源与改编说明

来源：[CopilotKit/OpenIntelligentUI](https://github.com/CopilotKit/OpenIntelligentUI)。固定版本：`f6e4388b26a64b9a0714943b08a1ce622b924eec`。

本地 `frontend-src/src/components/open-intelligent-ui/schema.ts` 改编自上游 `apps/app/src/components/generative-ui/open-generative-ui/` 的 `schema.ts`，只保留完成态协议和它的校验：内容必须是完成态，格式不对就返回空；脚本字段有内容时，首页退回文字版。

首页报告把这份完成态内容（只有 HTML 与 CSS）写进自己的 Shadow DOM 显示，见 [市场报告：图文版](open-intelligent-ui-preview.md)。PR #253 曾经从上游改编过 `renderer.tsx`、`frame-content.ts` 和 `websandbox-loader.ts`，并依赖 `@jetbrains/websandbox` 1.2.1，在 iframe 里显示报告。生产页面的 CSP 会被 srcdoc 沙箱框架继承，Websandbox 的内联启动脚本在生产里不能运行，所以这些文件和这个依赖都已移除。仓库里不再有来自上游的渲染代码，也不再依赖该包。

以下完整保留上游 MIT 许可证：

```text
The MIT License

Copyright (c) Atai Barkai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
